# main.py
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta, timezone
from asyncio import create_task, sleep
from concurrent.futures import ThreadPoolExecutor
import time
import signal
from pathlib import Path
import re
from typing import Optional

from PerfectionBot.config.yamlHandler import get_value
from PerfectionBot.scripts.filter import check_bad
from PerfectionBot.scripts import watchdog, yt, verify
from PerfectionBot.scripts.lockdown import initiate_lockdown, handle_confirm, handle_revoke
from PerfectionBot.scripts.log import log_to_channel
from PerfectionBot.scripts import leveling
from PerfectionBot.scripts.appeals import save_appeals, load_appeals

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(
    command_prefix=get_value("behaviour", "COMMAND_PREFIX"),
    intents=intents
)

try:
    raw_gid = get_value("GUILD_ID")
    GUILD_TEST_ID = int(raw_gid) if raw_gid else 944961657128497212
except Exception:
    GUILD_TEST_ID = 944961657128497212
TEST_GUILD = discord.Object(id=GUILD_TEST_ID)

executor = ThreadPoolExecutor()

flag_memory: dict[int, dict[int, dict]] = {}
_flag_msgs: dict[int, discord.Message] = {}
_xp_msgs: dict[int, discord.Message] = {}
verify_msg_ids: dict[int, int] = {}
_save_queue: set[int] = set()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

banned_keywords: set[str] = set()
BANNED_FILE = Path("banned-keywords.config")
APPEALS_PATH = DATA_DIR / "appeals.json"

appeals: dict[str, dict] = {}

FLAGS_FILE = DATA_DIR / "flags.dat"
XP_FILE = Path(leveling.FILE)
xp_memory: dict[int, int] = {}
_xp_initialized = False
_xp_lock = asyncio.Lock()

def sys_enabled(name: str) -> bool:
    try:
        val = get_value("systems", name)
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            s = val.strip().lower()
            return s in ("1", "true", "yes", "on", "y", "t")
        return bool(val)
    except Exception:
        return False

async def _run_with_semaphore(coros, limit=6):
    sem = asyncio.Semaphore(limit)
    async def sem_task(coro):
        async with sem:
            try:
                return await coro
            except Exception:
                return None
    return await asyncio.gather(*(sem_task(c) for c in coros), return_exceptions=True)

def load_banned_keywords():
    global banned_keywords
    newset = set()
    if BANNED_FILE.exists():
        try:
            with BANNED_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    newset.add(line.lower())
        except Exception as e:
            print(f"[load_banned_keywords] failed to read file: {e}")
            newset = set()
    banned_keywords = newset
    return banned_keywords

load_banned_keywords()

def parse_flags_lines(lines, guild_id=None):
    out = {}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split(":")
        if len(parts) == 2:
            try:
                uid = int(parts[0].strip())
                amt = int(parts[1].strip())
                out[uid] = {"flags_total": amt}
            except Exception:
                continue
        elif len(parts) == 3:
            try:
                gid = int(parts[0].strip())
                uid = int(parts[1].strip())
                amt = int(parts[2].strip())
                if guild_id is None or guild_id == gid:
                    out.setdefault(uid, {"flags_total": amt})
            except Exception:
                continue
    return out

def load_flags_from_file_global():
    data = {}
    if not FLAGS_FILE.exists():
        return data
    try:
        with FLAGS_FILE.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ":" not in ln:
                    continue
                parts = ln.split(":", 2)
                if len(parts) != 3:
                    continue
                try:
                    gid = int(parts[0].strip())
                    uid = int(parts[1].strip())
                    amt = int(parts[2].strip())
                    data.setdefault(gid, {})[uid] = {"flags_total": amt}
                except Exception:
                    continue
    except Exception as e:
        print(f"[load_flags_from_file_global] failed: {e}")
    return data

async def write_flags_file_from_memory():
    try:
        def _write():
            try:
                FLAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with FLAGS_FILE.open("w", encoding="utf-8") as f:
                    for gid, users in flag_memory.items():
                        for uid, data in users.items():
                            f.write(f"{gid}:{uid}:{data.get('flags_total', 0)}\n")
            except Exception as e:
                print(f"[write_flags_file_from_memory._write] failed: {e}")
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[write_flags_file_from_memory] scheduling write failed: {e}")

async def _load_flags(guild: discord.Guild):
    data = {}
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if mem:
        try:
            pinned = await mem.pins()
            for p in pinned:
                if p.content.startswith("[FLAGS]\n"):
                    body = p.content.split("\n", 1)[1] if "\n" in p.content else ""
                    lines = body.splitlines()
                    parsed = parse_flags_lines(lines, guild_id=guild.id)
                    if parsed:
                        flag_memory[guild.id] = parsed
                        _flag_msgs[guild.id] = p
                        try:
                            await write_flags_file_from_memory()
                        except Exception as e:
                            print(f"[load_flags] write_flags_file_from_memory failed: {e}")
                        return parsed
        except Exception as e:
            print(f"[load_flags] reading pins failed: {e}")
    try:
        global_data = await asyncio.to_thread(load_flags_from_file_global)
    except Exception as e:
        print(f"[load_flags] load_flags_from_file_global failed: {e}")
        global_data = {}
    if guild.id in global_data:
        flag_memory[guild.id] = global_data[guild.id].copy()
        return flag_memory[guild.id]
    return {}

async def _save_flags(guild: discord.Guild):
    try:
        mem = discord.utils.get(guild.text_channels, name="bot-mem")
        if not mem:
            try:
                mem = await guild.create_text_channel(
                    "bot-mem",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        guild.me: discord.PermissionOverwrite(read_messages=True)
                    }
                )
            except Exception as e:
                print(f"[save_flags] failed to create bot-mem channel: {e}")
                return
        flag_users = flag_memory.get(guild.id, {})
        try:
            await write_flags_file_from_memory()
        except Exception as e:
            print(f"[save_flags] write_flags_file_from_memory failed: {e}")
        msg = _flag_msgs.get(guild.id)
        body = ""
        for uid, data in flag_users.items():
            body += f"{uid}:{data.get('flags_total',0)}\n"
        content = "[FLAGS]\n" + body
        if msg and not getattr(msg, "deleted", False):
            try:
                await msg.edit(content=content)
            except Exception as e:
                print(f"[save_flags] edit pinned msg failed: {e}")
                msg = None
        if not msg:
            try:
                pinned = await mem.pins()
                found = None
                for p in pinned:
                    if p.content.startswith("[FLAGS]\n"):
                        found = p
                        break
                if found:
                    _flag_msgs[guild.id] = found
                    try:
                        await found.edit(content=content)
                        msg = found
                    except Exception as e:
                        print(f"[save_flags] edit found pinned msg failed: {e}")
                        msg = None
            except Exception as e:
                print(f"[save_flags] failed to inspect pins: {e}")
        if not msg:
            try:
                sent = await mem.send(content)
                await sent.pin()
                _flag_msgs[guild.id] = sent
            except Exception as e:
                print(f"[save_flags] send+pin failed: {e}")
    except Exception as e:
        print(f"[save_flags] unexpected error: {e}")

def _queue_flag_save(guild_id: int):
    _save_queue.add(guild_id)

async def _ensure_channels(guild: discord.Guild):
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if not mem:
        try:
            mem = await guild.create_text_channel(
                "bot-mem",
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True)
                }
            )
        except Exception as e:
            print(f"[ensure_channels] failed to create bot-mem: {e}")
            return
    await _load_flags(guild)
    try:
        pinned = await mem.pins()
        for p in pinned:
            if p.content.startswith("[FLAGS]\n"):
                _flag_msgs[guild.id] = p
                break
    except Exception as e:
        print(f"[ensure_channels] failed to read pins: {e}")

async def _load_xp_from_pin_message(msg: discord.Message) -> dict[int, int]:
    data = {}
    body = msg.content.split("\n", 1)[1] if "\n" in msg.content else ""
    for ln in body.splitlines():
        ln = ln.strip()
        if not ln or ":" not in ln:
            continue
        uid_s, xp_s = ln.split(":", 1)
        try:
            uid = int(uid_s.strip())
            xp = int(xp_s.strip())
            data[uid] = xp
        except Exception:
            continue
    return data

async def _load_xp_prefer_pins(guild: discord.Guild):
    global _xp_initialized, xp_memory
    if not sys_enabled("leveling"):
        return
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if not mem:
        return
    try:
        pinned = await mem.pins()
    except Exception as e:
        print(f"[load_xp_prefer_pins] pins failed: {e}")
        return
    for p in pinned:
        if p.content.startswith("[XP]\n"):
            data = await _load_xp_from_pin_message(p)
            if data:
                if not _xp_initialized:
                    xp_memory = data.copy()
                    _xp_initialized = True
                _xp_msgs[guild.id] = p
                return

async def _ensure_xp_msg_for_guild(guild: discord.Guild):
    if not sys_enabled("leveling"):
        return
    mem = discord.utils.get(guild.text_channels, name="bot-mem")
    if not mem:
        try:
            mem = await guild.create_text_channel(
                "bot-mem",
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True)
                }
            )
        except Exception as e:
            print(f"[ensure_xp_msg_for_guild] failed to create bot-mem: {e}")
            return
    try:
        pinned = await mem.pins()
    except Exception as e:
        pinned = []
        print(f"[ensure_xp_msg_for_guild] failed to read pins: {e}")
    for p in pinned:
        if p.content.startswith("[XP]\n"):
            _xp_msgs[guild.id] = p
            return
    try:
        content = "[XP]\n"
        for uid, xp in xp_memory.items():
            content += f"{uid}:{xp}\n"
        sent = await mem.send(content)
        await sent.pin()
        _xp_msgs[guild.id] = sent
    except Exception as e:
        print(f"[ensure_xp_msg_for_guild] create xp msg failed: {e}")

async def _push_xp_to_mem_for_guild(guild: discord.Guild):
    if not sys_enabled("leveling"):
        return
    try:
        mem = discord.utils.get(guild.text_channels, name="bot-mem")
        if not mem:
            try:
                mem = await guild.create_text_channel(
                    "bot-mem",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        guild.me: discord.PermissionOverwrite(read_messages=True)
                    }
                )
            except Exception as e:
                print(f"[push_xp_to_mem_for_guild] create bot-mem failed: {e}")
                return
        body = ""
        for uid, xp in xp_memory.items():
            body += f"{uid}:{xp}\n"
        content = "[XP]\n" + body
        msg = _xp_msgs.get(guild.id)
        if msg and not getattr(msg, "deleted", False):
            try:
                await msg.edit(content=content)
                return
            except Exception as e:
                print(f"[push_xp] edit xp msg failed: {e}")
                msg = None
        try:
            pinned = await mem.pins()
            found = None
            for p in pinned:
                if p.content.startswith("[XP]\n"):
                    found = p
                    break
            if found:
                _xp_msgs[guild.id] = found
                try:
                    await found.edit(content=content)
                    return
                except Exception as e:
                    print(f"[push_xp] edit found pinned xp msg failed: {e}")
        except Exception as e:
            print(f"[push_xp] reading pins failed: {e}")
        try:
            sent = await mem.send(content)
            await sent.pin()
            _xp_msgs[guild.id] = sent
        except Exception as e:
            print(f"[push_xp] send+pin xp msg failed: {e}")
    except Exception as e:
        print(f"[push_xp_to_mem_for_guild] unexpected error: {e}")

async def handle_message_event(message, *, is_edit=False, before_msg=None):
    if message.author.bot or not message.guild:
        return

    guild_id, user_id = message.guild.id, message.author.id

    hit = None
    if sys_enabled("filter"):
        try:
            if any(r.permissions.administrator for r in message.author.roles) and not get_value("behaviour", "flags", "FILTER_AFFECTS_ADMINS"):
                return
        except Exception:
            pass
        hit = await bot.loop.run_in_executor(executor, check_bad, message.content)

    if not hit and not is_edit and sys_enabled("leveling"):
        try:
            prev_xp = await asyncio.to_thread(leveling.read_xp, user_id)
        except Exception:
            prev_xp = xp_memory.get(user_id, 0)

        new_xp = prev_xp + 2
        try:
            await asyncio.to_thread(leveling.write_xp, user_id, 2)
        except Exception:
            pass

        async with _xp_lock:
            xp_memory[user_id] = new_xp

        prev_lvl = await bot.loop.run_in_executor(executor, leveling.convertToLevel, prev_xp)
        lvl = await bot.loop.run_in_executor(executor, leveling.convertToLevel, new_xp)

        if lvl > prev_lvl:
            new_role = None
            try:
                new_role = await leveling.check_level_reward(message.author, lvl)
            except Exception as e:
                print(f"[Leveling] Failed to assign role: {e}")

            color = new_role.color if new_role else (
                message.author.top_role.color if message.author.top_role else discord.Color.gold()
            )

            chnl_id = get_value("LEVELING", "CHANNEL_ID")
            chnl = bot.get_channel(int(chnl_id)) if chnl_id else None
            if chnl:
                new_embed = discord.Embed(
                    title=get_value("LEVELING", "EMBED", "title"),
                    description=f"<@{user_id}> " + get_value("LEVELING", "EMBED", "description"),
                    color=get_level_role_color(message.author)
                )
                new_embed.add_field(
                    name=get_value("LEVELING", "EMBED", "field"),
                    value=f"**{prev_lvl}** -> **{lvl}**",
                    inline=False
                )
                if new_role:
                    new_embed.add_field(
                        name="Unlocked Role",
                        value=f"{new_role.mention}",
                        inline=False
                    )
                await chnl.send(embed=new_embed)

        try:
            await _push_xp_to_mem_for_guild(message.guild)
        except Exception:
            pass
        return

    if not hit:
        return

    try:
        await message.delete()
    except Exception:
        pass

    flagged_word = hit.get("word", "unknown")

    user_mem = flag_memory.setdefault(guild_id, {}).setdefault(user_id, {"flags_total": 0})
    user_mem["flags_total"] += 1
    _queue_flag_save(guild_id)

    create_task(
        log_to_channel(
            message.guild,
            f"[WARN] {message.author.mention} for `{flagged_word}`\n\nContext: `{message.content}`",
            discord.Color.yellow(),
            "warn"
        )
    )

    try:
        content = message.content.replace("```", "´´´")
        prefix = "(Edited) " if is_edit else ""
        tmpl = get_value("behaviour", "flags", "WARN_DM") + f"\n\n```{content}```"
        dm_msg = await message.author.send(prefix + tmpl.format(word=flagged_word))
        await dm_msg.add_reaction("⚠️")

        appeals[str(dm_msg.id)] = {
            "user_id": user_id,
            "guild_id": guild_id,
            "warn_time": datetime.now(timezone.utc).isoformat(),
            "context": message.content,
            "reason": flagged_word,
            "status": "warned",
            "review_msg_id": None,
            "review_time": None,
            "review_by": None
        }
        save_appeals()
    except Exception:
        create_task(log_to_channel(message.guild, f"❌ Warn DM failed", discord.Color.red(), "fail"))

    total_flags = user_mem["flags_total"]

    if total_flags % 5 == 0:
        try:
            t = int(get_value("behaviour", "flags", "MUTE_TIME"))
            until = datetime.now(timezone.utc) + timedelta(seconds=t)
            await message.author.timeout(until, reason="Flag multiple timeout")
            create_task(
                log_to_channel(
                    message.guild,
                    f"🔇 Timed out {message.author.mention} for reaching {total_flags} flags ({t}s)",
                    discord.Color.orange(),
                    "mute"
                )
            )
        except Exception:
            create_task(log_to_channel(message.guild, f"❌ Timeout failed", discord.Color.red(), "fail"))

    limit = int(get_value("behaviour", "flags", "FLAG_LIMIT"))
    if total_flags >= limit:
        create_task(initiate_lockdown(message.guild, message.author, "flag_limit", "confirm"))

    _queue_flag_save(guild_id)

@tasks.loop(seconds=60)
async def push_xp_to_mem():
    if not sys_enabled("leveling"):
        return
    coros = [_push_xp_to_mem_for_guild(g) for g in bot.guilds]
    if coros:
        await _run_with_semaphore(coros, limit=6)

@tasks.loop(seconds=5)
async def flush_flag_saves():
    to_save = list(_save_queue)
    _save_queue.clear()
    if not to_save:
        return
    coros = []
    for gid in to_save:
        guild = bot.get_guild(gid)
        if guild:
            coros.append(_save_flags(guild))
    if coros:
        await _run_with_semaphore(coros, limit=6)

@tasks.loop(seconds=60)
async def push_flags_to_mem():
    coros = []
    for gid in list(flag_memory.keys()):
        guild = bot.get_guild(gid)
        if guild:
            coros.append(_save_flags(guild))
    if coros:
        await _run_with_semaphore(coros, limit=6)

@tasks.loop(seconds=60)
async def reload_banned_keywords_task():
    if not sys_enabled("filter"):
        return
    try:
        await asyncio.to_thread(load_banned_keywords)
    except Exception as e:
        print(f"[reload_banned_keywords_task] failed: {e}")

_monitor_last = time.perf_counter()
@tasks.loop(seconds=2)
async def monitor_lag():
    global _monitor_last
    now = time.perf_counter()
    delay = now - _monitor_last - 2
    _monitor_last = now
    if delay > 0.1:
        print(f"⚠️ Event loop lag detected: {delay:.3f}s")

@tasks.loop(minutes=1)
async def appeal_timeouts():
    now = datetime.now(timezone.utc)
    for dm_msg_id, appeal in list(appeals.items()):
        if appeal.get("status") == "appealed":
            try:
                review_time = datetime.fromisoformat(appeal.get("review_time"))
            except Exception:
                review_time = None
            if not review_time:
                continue
            if now - review_time > timedelta(hours=24):
                appeal["status"] = "timed_out"
                appeal["review_time"] = now.isoformat()
                appeals[dm_msg_id] = appeal
                save_appeals()
                try:
                    uobj = await bot.fetch_user(appeal["user_id"])
                    await uobj.send("⏳ No moderator reviewed your appeal within 24 hours — appeal timed out.")
                except Exception:
                    pass
                gobj = bot.get_guild(appeal.get("guild_id"))
                if gobj:
                    create_task(log_to_channel(gobj, f"⚪ Appeal timed out for <@{appeal['user_id']}>", discord.Color.dark_grey(), "info"))

@bot.event
async def on_ready():
    try:
        await watchdog.setup(bot)
        print("[on_ready] watchdog.setup succeeded")
    except Exception as e:
        print(f"[on_ready] watchdog.setup failed: {e}")
        try:
            await bot.add_cog(watchdog.WatchdogCog(bot))
            existing = [c.name for c in bot.tree.get_commands()]
            if watchdog.watchdog_group.name not in existing:
                try:
                    bot.tree.add_command(watchdog.watchdog_group)
                except Exception as e2:
                    print(f"[on_ready] bot.tree.add_command failed in fallback: {e2}")
            print("[on_ready] fallback added watchdog cog and group (or group already existed).")
        except Exception as e2:
            print(f"[on_ready] fallback adding watchdog cog failed: {e2}")

    try:
        raw = get_value("LOG_ID")
        alert_id = int(raw) if raw is not None else None
    except Exception:
        alert_id = None
    try:
        interval = int(get_value("watchdog", "check_interval"))
    except Exception:
        interval = None
    try:
        asyncio.create_task(watchdog.start_monitoring(bot, alert_channel_id=alert_id, interval=interval))
    except Exception as e:
        print(f"[on_ready] starting watchdog monitoring failed: {e}")

    coros = []
    for guild in bot.guilds:
        coros.append(_ensure_channels(guild))
    if coros:
        await _run_with_semaphore(coros, limit=6)

    coros2 = []
    for guild in bot.guilds:
        async def _do_guild_init(g=guild):
            try:
                if sys_enabled("leveling"):
                    try:
                        await _load_xp_prefer_pins(g)
                    except Exception as e:
                        print(f"[on_ready] _load_xp_prefer_pins failed for {g.id}: {e}")
                    try:
                        await _ensure_xp_msg_for_guild(g)
                    except Exception as e:
                        print(f"[on_ready] _ensure_xp_msg_for_guild failed for {g.id}: {e}")
            except Exception:
                pass

            try:
                verify_channel_id = int(get_value("VERIFY_ID"))
                ch = g.get_channel(verify_channel_id)
                if ch:
                    verify_msg = await verify.GetVerifyMsg(ch)
                    verify_msg_ids[g.id] = verify_msg.id
            except Exception:
                pass
        coros2.append(_do_guild_init())
    if coros2:
        await _run_with_semaphore(coros2, limit=6)

    try:
        global_flags = await asyncio.to_thread(load_flags_from_file_global)
        for gid, users in global_flags.items():
            flag_memory.setdefault(gid, {}).update(users)
    except Exception as e:
        print(f"[on_ready] loading global flags failed: {e}")

    try:
        if sys_enabled("yt"):
            bot.loop.create_task(yt.monitor_channel(bot))
    except Exception as e:
        print(f"[on_ready] starting yt.monitor_channel failed: {e}")

    try:
        flush_flag_saves.start()
        monitor_lag.start()
        push_flags_to_mem.start()

        if sys_enabled("filter"):
            reload_banned_keywords_task.start()

        if sys_enabled("leveling"):
            push_xp_to_mem.start()

        appeal_timeouts.start()
    except Exception as e:
        print(f"[on_ready] starting tasks failed: {e}")

    try:
        guild_raw = None
        try:
            guild_raw = get_value("GUILD_ID")
        except Exception:
            guild_raw = None

        if guild_raw:
            try:
                guild_id = int(guild_raw)
                await bot.tree.sync(guild=discord.Object(id=guild_id))
                print(f"Slash commands synced to guild {guild_id}.")
            except Exception as e:
                print(f"[on_ready] guild sync failed for {guild_raw}: {e}")
                try:
                    await bot.tree.sync()
                    print("Global slash commands synced (fallback).")
                except Exception as e2:
                    print(f"[on_ready] global sync fallback failed: {e2}")
        else:
            try:
                await bot.tree.sync()
                print("Global slash commands synced.")
            except Exception as e:
                print(f"[on_ready] global sync failed: {e}")
    except Exception as e:
        print(f"[on_ready] sync logic failed: {e}")

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    if payload.guild_id is None:
        ap = appeals.get(str(payload.message_id))
        if not ap:
            return
        if ap.get("status") != "warned":
            return
        if ap.get("user_id") != payload.user_id:
            return
        if str(payload.emoji) != "⚠️":
            return
        try:
            warn_time = datetime.fromisoformat(ap["warn_time"])
        except Exception:
            warn_time = None
        if warn_time and datetime.now(timezone.utc) - warn_time > timedelta(hours=24):
            ap["status"] = "timed_out"
            ap["review_time"] = datetime.now(timezone.utc).isoformat()
            appeals[str(payload.message_id)] = ap
            save_appeals()
            try:
                user_obj = await bot.fetch_user(ap["user_id"])
                await user_obj.send("❌ Appeal failed: appeal window of 24 hours has expired.")
            except Exception:
                pass
            return
        guild = bot.get_guild(ap["guild_id"])
        if not guild:
            return
        try:
            review_ch_id = int(get_value("behaviour", "flags", "review_channel"))
        except Exception:
            review_ch_id = None
        review_ch = guild.get_channel(review_ch_id) if review_ch_id else None
        if not review_ch:
            try:
                user_obj = await bot.fetch_user(ap["user_id"])
                await user_obj.send("❌ Appeal failed: review channel not configured or not found.")
            except Exception:
                pass
            return
        context = ap.get("context", "")
        preview = context
        if len(preview) > 1900:
            preview = preview[:1900] + "... (truncated)"
        reason = ap.get("reason", "warning")
        orig_user = ap["user_id"]
        try:
            review_msg = await review_ch.send(
                f"🔔 Appeal from <@{orig_user}> — reason: `{reason}`\n\n"
                f"Context:\n```{preview}```\n\n"
                "Moderators: react ✅ to accept (remove 1 flag) or ❌ to reject. (First moderator reaction decides.)"
            )
            await review_msg.add_reaction("✅")
            await review_msg.add_reaction("❌")
        except Exception:
            review_msg = None
        ap["status"] = "appealed"
        if review_msg:
            ap["review_msg_id"] = review_msg.id
            ap["review_time"] = datetime.now(timezone.utc).isoformat()
        ap["review_by"] = None
        appeals[str(payload.message_id)] = ap
        save_appeals()
        try:
            user_obj = await bot.fetch_user(orig_user)
            await user_obj.send("✅ Your appeal was submitted to moderators for review.")
        except Exception:
            pass
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    if verify_msg_ids.get(payload.guild_id) == payload.message_id and str(payload.emoji) == "✅":
        member = guild.get_member(payload.user_id)
        if not member:
            return
        try:
            await verify.add_role(guild, member)
            create_task(log_to_channel(guild, f"✅ Verified {member.mention}", discord.Color.green(), "verify"))
        except Exception:
            pass
    for dm_msg_id, ap in list(appeals.items()):
        if ap.get("review_msg_id") != payload.message_id:
            continue
        if ap.get("status") != "appealed":
            continue
        member = guild.get_member(payload.user_id)
        if not member:
            return
        if not member.guild_permissions.ban_members:
            return
        emoji = str(payload.emoji)
        if emoji == "✅":
            target_uid = ap["user_id"]
            gm = ap["guild_id"]
            gm_flags = flag_memory.setdefault(gm, {})
            user_flags = gm_flags.setdefault(target_uid, {"flags_total": 0})
            before = user_flags["flags_total"]
            user_flags["flags_total"] = max(before - 1, 0)
            ap["status"] = "accepted"
            ap["review_by"] = payload.user_id
            ap["review_time"] = datetime.now(timezone.utc).isoformat()
            appeals[dm_msg_id] = ap
            save_appeals()
            try:
                await _save_flags(bot.get_guild(gm))
            except Exception:
                pass
            try:
                uobj = await bot.fetch_user(target_uid)
                await uobj.send("✅ Your appeal was accepted by moderators. 1 flag removed.")
            except Exception:
                pass
            create_task(log_to_channel(bot.get_guild(gm) or guild, f"🟢 Appeal accepted for <@{target_uid}> by {member.mention}", discord.Color.blurple(), "info"))
            return
        if emoji == "❌":
            ap["status"] = "rejected"
            ap["review_by"] = payload.user_id
            ap["review_time"] = datetime.now(timezone.utc).isoformat()
            appeals[dm_msg_id] = ap
            save_appeals()
            try:
                uobj = await bot.fetch_user(ap["user_id"])
                await uobj.send("❌ Your appeal was rejected by moderators.")
            except Exception:
                pass
            create_task(log_to_channel(guild, f"🔴 Appeal rejected for <@{ap['user_id']}> by {member.mention}", discord.Color.blurple(), "info"))
            return

@bot.event
async def on_message(message: discord.Message):
    create_task(handle_message_event(message, is_edit=False))
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot or not after.guild:
        return
    if getattr(before, "content", None) == getattr(after, "content", None):
        return
    create_task(handle_message_event(after, is_edit=True, before_msg=before))

class CtxWrapper:
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.guild = interaction.guild
        self.author = interaction.user
        self.channel = interaction.channel

    async def send(self, *args, **kwargs):
        try:
            if not self.interaction.response.is_done():
                await self.interaction.response.send_message(*args, **kwargs)
            else:
                await self.interaction.followup.send(*args, **kwargs)
        except Exception:
            try:
                await self.interaction.followup.send(*args, **kwargs)
            except Exception:
                pass

    async def reply(self, *args, **kwargs):
        await self.send(*args, **kwargs)

@bot.tree.command(name="flags", description="Lists amount of flags for selected user or all flags.", guild=TEST_GUILD)
@app_commands.describe(user="User mention, ID or no argument to list all")
async def flags_cmd(interaction: discord.Interaction, user: Optional[str] = None):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    gm = interaction.guild.id
    if user is None or (isinstance(user, str) and user.lower() == "all"):
        mem = flag_memory.get(gm, {})
        flagged = [(uid, data) for uid, data in mem.items() if data.get("flags_total", 0)]
        embed = discord.Embed(title="Flagged Members", color=discord.Color.orange() if flagged else discord.Color.green())
        if flagged:
            for uid, data in flagged:
                member = interaction.guild.get_member(uid)
                embed.add_field(name=str(member) if member else f"<@{uid}>", value=f"Total Flags: {data.get('flags_total', 0)}", inline=False)
        else:
            embed.description = "None"
        await interaction.response.send_message(embed=embed)
        return

    try:
        if user.startswith("<@") and user.endswith(">"):
            user = user.strip("<@!>")
        uid = int(user)
    except Exception:
        await interaction.response.send_message("❌ Invalid user format. Use a mention or numeric ID.", ephemeral=True)
        return

    user_data = flag_memory.get(gm, {}).get(uid)
    member = interaction.guild.get_member(uid)
    member_name = str(member) if member else f"<@{uid}>"
    if not user_data:
        embed = discord.Embed(title=f"Flags for {member_name}", description="No flags found.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
        return
    embed = discord.Embed(title=f"Flags for {member_name}", color=discord.Color.orange())
    embed.add_field(name="Total Flags", value=str(user_data.get("flags_total", 0)), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="modflags", description="TBD", guild=TEST_GUILD)
@app_commands.describe(user="User mention or ID", amount="Amount to add (negative to subtract)")
async def modflags_cmd(interaction: discord.Interaction, user: str, amount: int):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    m = re.search(r"(\d{5,25})", user)
    if m:
        try:
            uid = int(m.group(1))
        except Exception:
            await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
            return
    else:
        try:
            uid = int(user)
        except Exception:
            await interaction.response.send_message("❌ Invalid user ID or mention format.", ephemeral=True)
            return

    gm = interaction.guild.id
    um = flag_memory.setdefault(gm, {}).setdefault(uid, {"flags_total": 0})

    before = um.get("flags_total", 0)
    um["flags_total"] = max(before + amount, 0)

    member = interaction.guild.get_member(uid)
    member_name = str(member) if member else f"<@{uid}>"

    await interaction.response.send_message(f"✅ {member_name} total flags: {before} → {um['flags_total']}")

    create_task(log_to_channel(
        interaction.guild,
        f"🛠 Admin adjusted total flags for {member_name}: {before} → {um['flags_total']}",
        discord.Color.blurple(),
        "info"
    ))

    try:
        create_task(_save_flags(interaction.guild))
    except Exception:
        try:
            await _save_flags(interaction.guild)
        except Exception as e:
            print(f"[modflags] _save_flags failed: {e}")

@bot.tree.command(name="confirm", description="Confirms user penalty", guild=TEST_GUILD)
async def confirm_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    await interaction.response.defer()
    ctx = CtxWrapper(interaction)
    await handle_confirm(ctx, flag_memory, _save_flags)

@bot.tree.command(name="revoke", description="Cancels penalty and lockdown", guild=TEST_GUILD)
async def revoke_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    await interaction.response.defer()
    ctx = CtxWrapper(interaction)
    await handle_revoke(ctx, flag_memory, _save_flags)

@bot.tree.command(name="clear", description="Clears selected amount of messages", guild=TEST_GUILD)
@app_commands.describe(amount="Number of messages to clear (1-100)")
async def clear_cmd(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You do not have permission to clear messages.", ephemeral=True)
        return

    await interaction.response.defer()
    await interaction.channel.purge(limit=amount + 1)
    await log_to_channel(interaction.guild, f"🛠 {interaction.user.mention} cleared {amount} messages in {interaction.channel.mention}", discord.Color.blurple(), "clear")
    await interaction.followup.send(f"✅ Cleared {amount} messages.")

@bot.tree.command(name="ping", description="Legacy status check. Now used for a joke", guild=TEST_GUILD)
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")

@bot.tree.command(name="resetver", description="Removes verified role from all members and resends verify message", guild=TEST_GUILD)
async def resetver_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    await interaction.response.defer()
    result = await verify.ResetVerification(interaction.guild, verify_msg_ids)
    await interaction.followup.send(result)

@app_commands.describe(member="Member to mute", duration="Duration in seconds", reason="Reason for the mute")
@bot.tree.command(name="mute", description="Timeouts a member", guild=TEST_GUILD)
async def mute_cmd(interaction: discord.Interaction, member: discord.Member, duration: Optional[int] = 180, reason: Optional[str] = "No reason provided"):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ You do not have permission to timeout members.", ephemeral=True)
        return

    if member.top_role.position >= interaction.guild.me.top_role.position:
        await interaction.response.send_message("❌ Cannot timeout this member: role hierarchy prevents it.", ephemeral=True)
        return

    until = datetime.now(timezone.utc) + timedelta(seconds=duration)

    try:
        await member.edit(timed_out_until=until, reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Failed to timeout: missing permissions.", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to timeout: {e}", ephemeral=True)
        return

    embed = discord.Embed(
        title="Timeout",
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Duration", value=f"{duration} seconds", inline=False)
    embed.set_footer(text="Time of action")

    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass

    create_task(log_to_channel(
        interaction.guild,
        f"🔇 {member.mention} has been muted by {interaction.user.mention} for {duration} seconds. Reason: {reason}",
        discord.Color.orange(),
        "mute"
    ))

    await interaction.response.send_message(f"✅ Timed out {member.mention} for {duration} seconds.")

@bot.tree.command(name="unmute", description="Removes timeout from a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to unmute")
async def unmute_cmd(interaction: discord.Interaction, member: discord.Member):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ You do not have permission to unmute members.", ephemeral=True)
        return

    try:
        await member.edit(timed_out_until=None, reason=f"Unmuted by {interaction.user}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Failed to unmute: missing permissions.", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to unmute: {e}", ephemeral=True)
        return

    embed = discord.Embed(
        title="Timeout Lifted",
        description=f"You have been unmuted in **{interaction.guild.name}**.",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Time of action")

    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass

    create_task(log_to_channel(
        interaction.guild,
        f"🔊 {member.mention} has been unmuted by {interaction.user.mention}.",
        discord.Color.green(),
        "unmute"
    ))

    await interaction.response.send_message(f"✅ Unmuted {member.mention}.")

@bot.tree.command(name="kick", description="Kicks a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to kick", reason="Reason for the kick")
async def kick_cmd(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("❌ You do not have permission to kick members.", ephemeral=True)
        return

    if member.top_role.position >= interaction.guild.me.top_role.position:
        await interaction.response.send_message("❌ Cannot kick this member: role hierarchy prevents it.", ephemeral=True)
        return

    embed = discord.Embed(
        title="You have been kicked",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"From {interaction.guild.name} at")

    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass

    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Failed to kick: missing permissions.", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to kick: {e}", ephemeral=True)
        return

    create_task(log_to_channel(
        interaction.guild,
        f"👢 {member.mention} was kicked by {interaction.user.mention}. Reason: {reason}",
        discord.Color.red(),
        "kick"
    ))

    await interaction.response.send_message(f"✅ Kicked {member.mention}.")

@bot.tree.command(name="ban", description="Bans a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to ban", reason="Reason for the ban")
async def ban_cmd(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    if member.top_role.position >= interaction.guild.me.top_role.position:
        await interaction.response.send_message("❌ Cannot ban this member: role hierarchy prevents it.", ephemeral=True)
        return

    embed = discord.Embed(
        title="You have been banned",
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"From {interaction.guild.name} at")

    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass

    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Failed to ban: missing permissions.", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to ban: {e}", ephemeral=True)
        return

    create_task(log_to_channel(
        interaction.guild,
        f"🔨 {member.mention} was banned by {interaction.user.mention}. Reason: {reason}",
        discord.Color.dark_red(),
        "ban"
    ))

    await interaction.response.send_message(f"✅ Banned {member.mention}.")

@bot.tree.command(name="synclevels", description="Recalculates and reapplies level roles to all members", guild=TEST_GUILD)
async def sync_levels_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return

    try:
        manager_role_id = int(get_value("roles", "bot_manager_ID"))
    except Exception:
        manager_role_id = None

    if manager_role_id and all(r.id != manager_role_id for r in getattr(interaction.user, "roles", [])):
        await interaction.response.send_message("❌ You need the bot manager role to use this command.", ephemeral=True)
        return

    await interaction.response.send_message("⏳ Starting full level sync... This may take a while.")
    count = 0
    failed = 0

    for member in interaction.guild.members:
        if member.bot:
            continue
        try:
            xp = await asyncio.to_thread(leveling.read_xp, member.id)
            lvl = await asyncio.to_thread(leveling.convertToLevel, xp)
            await leveling.check_level_reward(member, lvl)
            count += 1
        except Exception as e:
            print(f"[SyncLevels] Failed for {member}: {e}")
            failed += 1
        await asyncio.sleep(1.5)

    await interaction.followup.send(f"✅ Level sync complete! Processed {count} members, {failed} failed.")

@bot.tree.command(name="lvl", description="Check user level", guild=TEST_GUILD)
@app_commands.describe(user="User to check (optional)")
async def level_check_cmd(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return

    target = user or interaction.user

    try:
        xp = await asyncio.to_thread(leveling.read_xp, target.id)
    except Exception:
        xp = xp_memory.get(target.id, 0)

    lvl = await asyncio.to_thread(leveling.convertToLevel, xp)
    color = get_level_role_color(target)

    embed = discord.Embed(title="📊 Level Info", color=color)
    embed.add_field(name="User", value=target.mention, inline=True)
    embed.add_field(name="Level", value=str(lvl), inline=True)
    embed.add_field(name="XP", value=str(xp), inline=True)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stop", description="Stops the bot. It may only be rebooted from the host device", guild=TEST_GUILD)
async def stop_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return

    try:
        manager_role_id = int(get_value("roles", "bot_manager_ID"))
    except Exception:
        manager_role_id = None

    has_manager_role = False
    if manager_role_id:
        has_manager_role = any(r.id == manager_role_id for r in getattr(interaction.user, "roles", []))

    if not (interaction.user.guild_permissions.administrator or has_manager_role):
        await interaction.response.send_message("❌ You do not have permission to stop the bot.", ephemeral=True)
        return

    await interaction.response.send_message("Bot is shutting down...")
    await shutdown()

class DMModal(discord.ui.Modal, title="Send DM"):
    target = discord.ui.TextInput(
        label="Target (user ID - optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=100,
        placeholder="Leave empty to DM everyone"
    )
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.long,
        required=True,
        max_length=4000,
        placeholder="Enter the message to send"
    )

    async def on_submit(self, modal_interaction: discord.Interaction):
        await modal_interaction.response.defer(thinking=True, ephemeral=True)
        targ = self.target.value.strip()
        body = self.message.value

        sent_count = 0
        failed_count = 0
        skipped_count = 0
        guild = modal_interaction.guild
        author = modal_interaction.user

        async def _send_to_user(user_obj):
            nonlocal sent_count, failed_count
            try:
                await user_obj.send(body)
                sent_count += 1
            except Exception:
                failed_count += 1

        if not targ:
            members = [m for m in guild.members if not m.bot]
            for m in members:
                await _send_to_user(m)
                await asyncio.sleep(0.35)

            summary = f"✅ DM broadcast complete. Sent: {sent_count}, Failed: {failed_count}, Skipped (bots): {skipped_count}."
            try:
                create_task(log_to_channel(guild, f"✉️ {author.mention} broadcasted a DM to the guild. Sent: {sent_count}, Failed: {failed_count}", discord.Color.blurple(), "dm"))
            except Exception:
                pass
            await modal_interaction.followup.send(summary, ephemeral=True)
            return

        m = re.search(r"(\d{5,25})", targ)
        if not m:
            await modal_interaction.followup.send("❌ Could not parse a user ID from the target. Use a mention or numeric ID.", ephemeral=True)
            return

        try:
            uid = int(m.group(1))
        except Exception:
            await modal_interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
            return

        member_obj = guild.get_member(uid)
        if member_obj:
            try:
                await _send_to_user(member_obj)
                await modal_interaction.followup.send(f"✅ Message sent to {member_obj.mention}. Sent: {sent_count}, Failed: {failed_count}.", ephemeral=True)
                try:
                    create_task(log_to_channel(guild, f"✉️ {author.mention} sent a DM to {member_obj.mention}. Sent: {sent_count}, Failed: {failed_count}", discord.Color.blurple(), "dm"))
                except Exception:
                    pass
                return
            except Exception:
                await modal_interaction.followup.send(f"❌ Sending DM to {member_obj.mention} failed.", ephemeral=True)
                return

        try:
            user_obj = await modal_interaction.client.fetch_user(uid)
        except Exception:
            await modal_interaction.followup.send("❌ Could not find a user with that ID.", ephemeral=True)
            return

        try:
            await _send_to_user(user_obj)
            await modal_interaction.followup.send(f"✅ Message sent to {user_obj}. Sent: {sent_count}, Failed: {failed_count}.", ephemeral=True)
            try:
                create_task(log_to_channel(guild, f"✉️ {author.mention} sent a DM to {user_obj}. Sent: {sent_count}, Failed: {failed_count}", discord.Color.blurple(), "dm"))
            except Exception:
                pass
        except Exception:
            await modal_interaction.followup.send("❌ Sending DM failed.", ephemeral=True)


@bot.tree.command(name="senddm", description="Sends dm to selected member or everyone.", guild=TEST_GUILD)
async def dm_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a guild.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    try:
        await interaction.response.send_modal(DMModal())
    except Exception as e:
        print(f"Modal open failed: {type(e).__name__}: {e}")
        await interaction.followup.send("❌ Failed to open DM modal.", ephemeral=True)

def get_level_role_color(member: discord.Member) -> discord.Color:
    level_roles = leveling.read_level_roles()
    member_level_roles = []

    for lvl, role_id in level_roles:
        role = member.guild.get_role(role_id)
        if role and role in member.roles:
            member_level_roles.append((lvl, role))

    if not member_level_roles:
        return discord.Color.default()

    _, top_role = max(member_level_roles, key=lambda x: x[0])

    return top_role.color if top_role.color != discord.Color.default() else discord.Color.light_gray()

async def main():
    await asyncio.to_thread(load_appeals)
    token = get_value("tokens", "bot")
    if not token:
        print("Bot token missing in config; exiting.")
        return
    await bot.start(token)

async def shutdown():
    try:
        await bot.close()
    except Exception:
        pass
    try:
        asyncio.get_event_loop().stop()
    except Exception:
        pass

def signal_handler(sig, frame):
    try:
        asyncio.create_task(shutdown())
    except Exception:
        pass

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except Exception:
        pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

#this is way longer than it should be. Fuck