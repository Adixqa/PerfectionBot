#main

import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta, timezone
from asyncio import create_task, sleep
from concurrent.futures import ThreadPoolExecutor
import time

from PerfectionBot.config.yamlHandler import get_value
from PerfectionBot.scripts.filter import check_bad
from PerfectionBot.scripts import yt, verify
from PerfectionBot.scripts.lockdown import initiate_lockdown, handle_confirm, handle_revoke
from PerfectionBot.scripts.log import log_to_channel

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(
    command_prefix=get_value("behaviour", "COMMAND_PREFIX"),
    intents=intents
)

executor = ThreadPoolExecutor()
flag_memory: dict[int, dict[int, dict]] = {}
_flag_msgs: dict[int, discord.Message] = {}
verify_msg_ids: dict[int, int] = {}
_save_queue: set[int] = set()

@tasks.loop(seconds=5)
async def flush_flag_saves():
    to_save = list(_save_queue)
    _save_queue.clear()
    for gid in to_save:
        guild = bot.get_guild(gid)
        if guild:
            try:
                await _save_flags(guild)
            except: pass

@tasks.loop(seconds=2)
async def monitor_lag():
    last = time.perf_counter()
    while True:
        await sleep(2)
        now = time.perf_counter()
        delay = now - last - 2
        last = now
        if delay > 0.1:
            print(f"⚠️ Event loop lag detected: {delay:.3f}s")

async def _load_flags(guild: discord.Guild):
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if not mem:
        return {}
    pinned = await mem.pins()
    if not pinned:
        msg = await mem.send("{}")
        await msg.pin()
        return {}
    data = json.loads(pinned[0].content)
    return {int(u): v for u, v in data.get(str(guild.id), {}).items()}

async def _save_flags(guild: discord.Guild):
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if not mem:
        return
    msg = _flag_msgs.get(guild.id)
    if not msg:
        pinned = await mem.pins()
        msg = pinned[0] if pinned else await mem.send("{}")
        await msg.pin()
        _flag_msgs[guild.id] = msg

    full = {
        str(g): {str(u): data for u, data in users.items()}
        for g, users in flag_memory.items()
    }
    await msg.edit(content=json.dumps(full, indent=2))

def _queue_flag_save(guild_id: int):
    _save_queue.add(guild_id)

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
    flag_memory[guild.id] = await _load_flags(guild)
    pinned = await mem.pins()
    if pinned:
        _flag_msgs[guild.id] = pinned[0]

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user} (ID: {bot.user.id})")
    for guild in bot.guilds:
        await _ensure_channels(guild)
        try:
            verify_channel_id = int(get_value("VERIFY_ID"))
            ch = guild.get_channel(verify_channel_id)
            if ch:
                verify_msg = await verify.GetVerifyMsg(ch)
                verify_msg_ids[guild.id] = verify_msg.id
            else:
                print(f"[verify] channel {verify_channel_id} not found in guild {guild.id}")
        except Exception as e:
            create_task(log_to_channel(guild, f"❌ Verify message fetch failed: {e}", discord.Color.red(), "fail"))

    bot.loop.create_task(yt.monitor_channel(bot))
    flush_flag_saves.start()
    monitor_lag.start()

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    if verify_msg_ids.get(payload.guild_id) != payload.message_id:
        return

    if str(payload.emoji) != "✅":
        return

    member = guild.get_member(payload.user_id)
    if not member:
        return

    await verify.add_role(guild, member)
    create_task(log_to_channel(
        guild,
        f"✅ Verified {member.mention}",
        discord.Color.green(),
        "verify"
    ))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    async def process():
        guild_id, user_id = message.guild.id, message.author.id

        if any(r.permissions.administrator for r in message.author.roles) \
           and not get_value("behaviour", "flags", "FILTER_AFFECTS_ADMINS"):
            return

        hit = await bot.loop.run_in_executor(executor, check_bad, message.content)
        if not hit:
            return

        try:
            await message.delete()
        except: pass

        word, evt, thresh = hit["word"], hit["event"], hit["count"]
        user_mem = flag_memory.setdefault(guild_id, {}).setdefault(user_id, {"flags_total": 0, "words": {}})
        word_counts = user_mem["words"]
        word_counts[word] = word_counts.get(word, 0) + 1
        user_mem["flags_total"] += 1

        _queue_flag_save(guild_id)
        create_task(log_to_channel(
            message.guild,
            f"[WARN] {message.author.mention} for `{word}`\n\nContext: `{message.content}`",
            discord.Color.yellow(),
            "warn"
        ))

        try:
            tmpl = get_value("behaviour", "flags", "WARN_DM")
            await message.author.send(tmpl.format(word=word))
        except Exception as e:
            create_task(log_to_channel(message.guild, f"❌ Warn DM failed: {e}", discord.Color.red(), "fail"))

        if word_counts[word] >= thresh:
            if evt == "mute":
                t = int(get_value("behaviour", "flags", "MUTE_TIME"))
                until = datetime.now(timezone.utc) + timedelta(seconds=t)
                try:
                    await message.author.timeout(until, reason="Blacklisted content")
                    create_task(log_to_channel(
                        message.guild,
                        f"🔇 Muted {message.author.mention} ({t}s)",
                        discord.Color.orange(),
                        "mute"
                    ))
                except Exception as e:
                    create_task(log_to_channel(message.guild, f"❌ Mute failed: {e}", discord.Color.red(), "fail"))
            else:
                create_task(initiate_lockdown(message.guild, message.author, word, evt))

        limit = int(get_value("behaviour", "flags", "FLAG_LIMIT"))
        if user_mem["flags_total"] >= limit:
            try:
                await message.guild.ban(
                    message.author,
                    reason=f"Reached {limit} flags",
                    delete_message_days=0
                )
                create_task(log_to_channel(
                    message.guild,
                    f"⛔ Auto-banned {message.author.mention} for reaching flag limit",
                    discord.Color.red(),
                    "ban"
                ))
                flag_memory[guild_id].pop(user_id, None)
                chan = discord.utils.get(
                    message.guild.text_channels,
                    name=f"lockdown-{user_id}"
                )
                if chan:
                    await chan.delete(reason="User auto-banned")
            except Exception as e:
                create_task(log_to_channel(message.guild, f"❌ Auto-ban failed: {e}", discord.Color.red(), "fail"))

        _queue_flag_save(guild_id)

    create_task(process())
    await bot.process_commands(message)

@bot.command(name="flags")
@commands.has_permissions(ban_members=True)
async def flags(ctx: commands.Context, user: str = None):
    gm = ctx.guild.id

    if user is None or user.lower() == "all":
        mem = flag_memory.get(gm, {})
        flagged = [(uid, data) for uid, data in mem.items() if data.get("flags_total", 0)]

        embed = discord.Embed(
            title="Flagged Members",
            color=discord.Color.orange() if flagged else discord.Color.green()
        )

        if flagged:
            for uid, data in flagged:
                member = ctx.guild.get_member(uid)
                embed.add_field(
                    name=str(member) if member else f"<@{uid}>",
                    value=f"Total Flags: {data.get('flags_total', 0)}",
                    inline=False
                )
        else:
            embed.description = "None"

        return await ctx.send(embed=embed)

    try :
        if user.startswith("<@") and user.endswith(">"):
            user = user.strip("<@!>")

        uid = int(user)
    except ValueError:
        return await ctx.send("❌ Invalid user format. Use a mention or numeric ID.")

    user_data = flag_memory.get(gm, {}).get(uid)
    member = ctx.guild.get_member(uid)
    member_name = str(member) if member else f"<@{uid}>"

    if not user_data:
        embed = discord.Embed(
            title=f"Flags for {member_name}",
            description="No flags found.",
            color=discord.Color.green()
        )
        return await ctx.send(embed=embed)

    embed = discord.Embed(
        title=f"Flags for {member_name}",
        color=discord.Color.orange()
    )
    embed.add_field(name="Total Flags", value=str(user_data.get("flags_total", 0)), inline=False)

    for word, count in user_data.get("words", {}).items():
        embed.add_field(name=word, value=str(count), inline=True)

    await ctx.send(embed=embed)

@bot.command(name="modflags")
@commands.has_permissions(ban_members=True)
async def modflags(ctx: commands.Context, user: str, amount: int, keyword: str = None):
    member = None
    if user.startswith("<@") and user.endswith(">"):
        user = user.strip("<@!>")
    try:
        uid = int(user)
        member = ctx.guild.get_member(uid)
    except ValueError:
        return await ctx.send("❌ Invalid user ID or mention format.")

    if not member:
        return await ctx.send("❌ User not found in this server.")

    gm, uid = ctx.guild.id, member.id
    um = flag_memory.setdefault(gm, {}).setdefault(uid, {"flags_total": 0, "words": {}})

    if keyword is None:
        before = um["flags_total"]
        um["flags_total"] = max(before + amount, 0)
        await ctx.send(f"✅ {member.mention} total flags: {before} → {um['flags_total']}")
        await log_to_channel(
            ctx.guild,
            f"🛠 Admin adjusted total flags for {member.mention}: {before} → {um['flags_total']}",
            discord.Color.blurple(),
            "info"
        )
    else:
        before = um["words"].get(keyword, 0)
        um["words"][keyword] = max(before + amount, 0)
        await ctx.send(f"✅ {member.mention} flags for {keyword}: {before} → {um['words'][keyword]}")
        await log_to_channel(
            ctx.guild,
            f"🛠 Admin adjusted {keyword} flags for {member.mention}: {before} → {um['words'][keyword]}",
            discord.Color.blurple(),
            "info"
        )
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
    await ctx.channel.purge(limit=amount + 1)
    await log_to_channel(
        ctx.guild,
        f"🛠 {ctx.author.mention} cleared {amount} messages in {ctx.channel.mention}",
        discord.Color.blurple(),
        "clear"
    )

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.reply("Ping!")

@bot.command(name="resetver")
@commands.has_permissions(administrator=True)
async def resetver(ctx: commands.Context):
    result = await verify.ResetVerification(ctx.guild, verify_msg_ids)
    await ctx.send(result)

if __name__ == "__main__":
    bot.run(get_value("tokens", "bot"))
