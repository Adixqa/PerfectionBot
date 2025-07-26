import discord
from discord.ext import commands
import json
from datetime import datetime, timedelta, timezone

# Replace these imports with your actual implementations
from PerfectionBot.config.yamlHandler import get_value
from PerfectionBot.scripts.filter import check_bad
from PerfectionBot.scripts import yt
from PerfectionBot.scripts.lockdown import (
    initiate_lockdown,
    handle_confirm,
    handle_revoke
)
from PerfectionBot.scripts.log import log_to_channel

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=get_value("behaviour", "COMMAND_PREFIX"),
    intents=intents
)

flag_memory: dict[int, dict[int, dict]] = {}
_flag_msgs: dict[int, discord.Message] = {}

async def _load_flags(guild: discord.Guild):
    ch = discord.utils.get(guild.text_channels, name="bot-mem")
    if not ch:
        return {}
    pinned = await ch.pins()
    if not pinned:
        msg = await ch.send("{}")
        await msg.pin()
        return {}
    data = json.loads(pinned[0].content)
    return {int(u): v for u, v in data.get(str(guild.id), {}).items()}

async def _save_flags(guild: discord.Guild):
    ch = discord.utils.get(guild.text_channels, name="bot-mem")
    if not ch:
        return
    msg = _flag_msgs.get(guild.id)
    if not msg:
        pinned = await ch.pins()
        msg = pinned[0] if pinned else await ch.send("{}")
        await msg.pin()
        _flag_msgs[guild.id] = msg

    full = {
        str(g): {str(u): data for u, data in users.items()}
        for g, users in flag_memory.items()
    }
    await msg.edit(content=json.dumps(full, indent=2))

async def _ensure_channels(guild: discord.Guild):
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if not mem:
        mem = await guild.create_text_channel(
            "bot-mem",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True)
            }
        )
    logs = discord.utils.get(guild.text_channels, name="bot-logs")
    if not logs:
        await guild.create_text_channel(
            "bot-logs",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True)
            }
        )

    flag_memory[guild.id] = await _load_flags(guild)
    pinned = await mem.pins()
    if pinned:
        _flag_msgs[guild.id] = pinned[0]

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user} (ID: {bot.user.id})")
    for g in bot.guilds:
        await _ensure_channels(g)
    bot.loop.create_task(yt.monitor_channel(bot))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    gid, uid = message.guild.id, message.author.id

    # Admin immunity config
    if any(r.permissions.administrator for r in message.author.roles) and not get_value("behaviour", "flags", "FILTER_AFFECTS_ADMINS"):
        return await bot.process_commands(message)
    
    context = message
    hit = check_bad(message.content)
    if not hit:
        return await bot.process_commands(message)

    try:
        await message.delete()
    except Exception:
        pass

    word, evt, thresh = hit["word"], hit["event"], hit["count"]

    user_mem = flag_memory.setdefault(gid, {}).setdefault(uid, {"flags_total":0,"words":{}})
    word_counts = user_mem["words"]
    word_counts[word] = word_counts.get(word, 0) + 1
    user_mem["flags_total"] += 1

    await _save_flags(message.guild)
    await log_to_channel(message.guild, f"[WARN] {message.author.mention} for `{word}`\n\nContext: `{context}`", discord.Color.yellow(), "warn")

    try:
        tmpl = get_value("behaviour", "flags", "WARN_DM")
        await message.author.send(tmpl.format(word=word))
    except Exception as e:
        await log_to_channel(message.guild, f"❌ Warn DM failed: {e}", discord.Color.red(), "fail")

    if word_counts[word] >= thresh:
        #await log_to_channel(message.guild, f"[{evt.upper()}] {message.author.mention} for `{word}`", discord.Color.orange())
        if evt == "mute":
            t = get_value("behaviour", "flags", "MUTE_TIME")
            until = datetime.now(timezone.utc) + timedelta(seconds=t)
            try:
                await message.author.timeout(until, reason="Blacklisted content")
                await log_to_channel(message.guild, f"🔇 Muted {message.author.mention} ({t}s)", discord.Color.orange(), "mute")
            except Exception as e:
                await log_to_channel(message.guild, f"❌ Mute failed: {e}", discord.Color.red(), "fail")
        else:
            await initiate_lockdown(message.guild, message.author, word, evt)

    limit = get_value("behaviour", "flags", "FLAG_LIMIT")
    if user_mem["flags_total"] >= limit:
        try:
            await message.guild.ban(
                message.author,
                reason=f"Reached {limit} flags",
                delete_message_days=0
            )
            await log_to_channel(message.guild, f"⛔ Auto-banned {message.author.mention} for reaching flag limit", discord.Color.red(), "ban")
            flag_memory[gid].pop(uid, None)
            chan = discord.utils.get(
                message.guild.text_channels,
                name=f"lockdown-{uid}"
            )
            if chan:
                await chan.delete(reason="User auto-banned")
        except Exception as e:
            await log_to_channel(message.guild, f"❌ Auto-ban failed: {e}", discord.Color.red(), "fail")

    await _save_flags(message.guild)
    await bot.process_commands(message)

@bot.command(name="flagged")
@commands.has_permissions(administrator=True)
async def flagged(ctx: commands.Context):
    mem = flag_memory.get(ctx.guild.id, {})
    lines = [f"{(ctx.guild.get_member(u) or f'<@{u}>')}: total flags={c['flags_total']}" 
             for u,c in mem.items() if c.get("flags_total",0)]
    await ctx.send("**Flagged members:**\n" + ("\n".join(lines) or "None"))

@bot.command(name="modflags")
@commands.has_permissions(administrator=True)
async def modflags(ctx: commands.Context, member: discord.Member, amount: int, keyword: str=None):
    gm, uid = ctx.guild.id, member.id
    um = flag_memory.setdefault(gm, {}).setdefault(uid, {"flags_total":0,"words":{}})
    if keyword is None:
        before = um["flags_total"]
        um["flags_total"] = max(before+amount,0)
        await ctx.send(f"✅ {member.mention} total flags: {before} → {um['flags_total']}")
        await log_to_channel(ctx.guild, f"🛠 Admin adjusted total flags for {member.mention}: {before} → {um['flags_total']},", discord.Color.blurple(), "info")
    else:
        before = um["words"].get(keyword,0)
        um["words"][keyword] = max(before+amount,0)
        await ctx.send(f"✅ {member.mention} flags for `{keyword}`: {before} → {um['words'][keyword]}", discord.Color.blurple(), "info")
        await log_to_channel(ctx.guild, f"🛠 Admin adjusted `{keyword}` flags for {member.mention}: {before} → {um['words'][keyword]}", discord.Color.blurple(), "info")
    await _save_flags(ctx.guild)

@bot.command(name="confirm")
@commands.has_permissions(ban_members=True)
async def confirm(ctx: commands.Context):
    await handle_confirm(ctx, flag_memory, _save_flags)

@bot.command(name="revoke")
@commands.has_permissions(ban_members=True)
async def revoke(ctx: commands.Context):
    await handle_revoke(ctx, flag_memory, _save_flags)

@bot.command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear(ctx: commands.Context, amount: int):
    if not 1 <= amount <= 100:
        return await ctx.reply("Choose between 1 and 100.")
    await ctx.channel.purge(limit=amount+1)
    await log_to_channel(ctx.guild, f"🛠 Cleared {amount} messages in {ctx.channel}", discord.Color.blurple(), "clear")

@bot.command(name="ping")
@commands.has_permissions(administrator=True)
async def ping(ctx: commands.Context):
    await ctx.reply("Ping!")

if __name__ == "__main__":
    bot.run(get_value("tokens", "bot"))