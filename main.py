import discord
from discord.ext import commands
import json
from datetime import datetime, timedelta, timezone

from PerfectionBot.config.yamlHandler import get_value
from PerfectionBot.scripts.filter import check_bad
from PerfectionBot.scripts import yt

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
    gdata = data.get(str(guild.id), {})
    return {int(u): val for u, val in gdata.items()}

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

async def _log(guild: discord.Guild, txt: str):
    logs = discord.utils.get(guild.text_channels, name="bot-logs")
    if logs:
        await logs.send(txt)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    gid, uid = message.guild.id, message.author.id

    if any(r.permissions.administrator for r in message.author.roles) \
       and not get_value("behaviour", "flags", "FILTER_AFFECTS_ADMINS"):
        return await bot.process_commands(message)

    hit = check_bad(message.content)
    if not hit:
        return await bot.process_commands(message)

    try:
        await message.delete()
    except:
        pass

    word, evt, thresh = hit["word"], hit["event"], hit["count"]

    user_mem = flag_memory.setdefault(gid, {}).setdefault(uid, {
        "flags_total": 0,
        "words": {}
    })

    word_counts = user_mem.setdefault("words", {})
    word_counts[word] = word_counts.get(word, 0) + 1

    user_mem["flags_total"] += 1

    await _save_flags(message.guild)
    await _log(message.guild, f"[WARN] {message.author.mention} for `{word}`")

    try:
        tmpl = get_value("behaviour", "flags", "WARN_DM")
        await message.author.send(tmpl.format(word=word))
    except Exception as e:
        await _log(message.guild, f"❌ Warn DM failed: {e}")

    if word_counts[word] >= thresh:
        await _log(message.guild, f"[{evt.upper()}] {message.author.mention} for `{word}`")

        if evt == "mute":
            t = get_value("behaviour", "flags", "MUTE_TIME")
            try:
                until = datetime.now(timezone.utc) + timedelta(seconds=t)
                await message.author.timeout(until, reason="Blacklisted content")
                await _log(message.guild, f"🔇 Muted {message.author.mention} ({t}s)")
            except Exception as e:
                await _log(message.guild, f"❌ Mute failed: {e}")

        elif evt == "kick":
            try:
                await message.guild.kick(message.author, reason="Blacklisted content")
                await _log(message.guild, f"👢 Kicked {message.author.mention}")
            except Exception as e:
                await _log(message.guild, f"❌ Kick failed: {e}")

        elif evt == "ban":
            try:
                await message.guild.ban(message.author, reason="Blacklisted content", delete_message_days=0)
                await _log(message.guild, f"⛔ Banned {message.author.mention}")
            except Exception as e:
                await _log(message.guild, f"❌ Ban failed: {e}")

    limit = get_value("behaviour", "flags", "FLAG_LIMIT")
    if user_mem["flags_total"] >= limit:
        try:
            await message.guild.ban(
                message.author,
                reason=f"Reached {limit} flags",
                delete_message_days=0
            )
            await _log(message.guild, f"⛔ Auto-banned {message.author.mention} for reaching flag limit")
        except Exception as e:
            await _log(message.guild, f"❌ Auto-ban failed: {e}")

    await _save_flags(message.guild)
    await bot.process_commands(message)

@bot.command(name="flagged")
async def flagged(ctx: commands.Context):
    mem = flag_memory.get(ctx.guild.id, {})
    lines = []
    for u, c in mem.items():
        if c.get("flags_total", 0):
            member = ctx.guild.get_member(u) or f"<@{u}>"
            lines.append(
                f"{member}: total flags={c.get('flags_total', 0)}"
            )
    await ctx.send("**Flagged members:**\n" + ("\n".join(lines) or "None"))

@bot.command(name="modflags")
@commands.has_permissions(administrator=True)
async def modflags(ctx: commands.Context, member: discord.Member, amount: int, keyword: str = None):
    gm, uid = ctx.guild.id, member.id
    um = flag_memory.setdefault(gm, {}).setdefault(uid, {
        "flags_total": 0,
        "words": {}
    })

    if keyword is None:
        # Adjust total flags
        before = um["flags_total"]
        um["flags_total"] = max(before + amount, 0)
        after = um["flags_total"]
        await ctx.send(f"✅ {member.mention} total flags: {before} → {after}")
        await _log(ctx.guild,
            f"🛠 Admin {ctx.author.mention} adjusted total flags for {member.mention}: {before} → {after}"
        )
    else:
        words = um.setdefault("words", {})
        before = words.get(keyword, 0)
        words[keyword] = max(before + amount, 0)
        after = words[keyword]
        await ctx.send(f"✅ {member.mention} flags for `{keyword}`: {before} → {after}")
        await _log(ctx.guild,
            f"🛠 Admin {ctx.author.mention} adjusted flags for '{keyword}' of {member.mention}: {before} → {after}"
        )

    await _save_flags(ctx.guild)

@bot.command()
async def ping(ctx):
    await ctx.message.reply("Ping!")

@bot.command()
async def clear(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.message.reply("Invalid number. Choose between 1 and 100.")
        return

    chn = ctx.channel
    deleted_messages = await ctx.channel.purge(limit=amount + 1)
    await _log(ctx.guild,
            f"🛠 Admin {ctx.author.mention} cleared {amount} messages in [{chn}]"
        )

if __name__ == "__main__":
    bot.run(get_value("tokens", "bot"))