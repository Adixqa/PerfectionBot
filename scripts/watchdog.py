# watchdog.py

import asyncio
import os
import sys
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from PerfectionBot.config.yamlHandler import get_value
from PerfectionBot.scripts.log import log_to_channel

try:
    import psutil
except Exception:
    psutil = None


def _format_bytes(n: Optional[int]) -> str:
    if n is None:
        return "N/A"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} PB"


async def collect_status(bot: discord.Client) -> dict:
    ram_used = ram_total = ram_available = ram_percent = None
    cpu_percent = None
    disk_used = disk_total = disk_percent = None

    if psutil:
        try:
            vm = psutil.virtual_memory()
            ram_total = vm.total
            ram_available = getattr(vm, "available", None)
            ram_used = ram_total - (ram_available if ram_available is not None else getattr(vm, "free", 0))
            ram_percent = vm.percent
        except Exception:
            ram_used = ram_total = ram_available = ram_percent = None

        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
        except Exception:
            try:
                cpu_percent = psutil.cpu_percent(interval=None)
            except Exception:
                cpu_percent = None

        try:
            du = psutil.disk_usage("/")
            disk_total = du.total
            disk_used = du.used
            disk_percent = du.percent
        except Exception:
            disk_total = disk_used = disk_percent = None
    else:
        try:
            with open("/proc/meminfo", "r") as f:
                data = f.read()
            lines = {l.split(":")[0]: l.split(":")[1].strip() for l in data.splitlines() if ":" in l}
            total_kb = int(lines.get("MemTotal", "0 kB").split()[0])
            avail_kb = int(lines.get("MemAvailable", "0 kB").split()[0])
            ram_total = total_kb * 1024
            ram_available = avail_kb * 1024
            ram_used = ram_total - ram_available
            ram_percent = (ram_used / ram_total) * 100 if ram_total else None
        except Exception:
            ram_used = ram_total = ram_available = ram_percent = None

        try:
            usage = shutil.disk_usage("/")
            disk_total = usage.total
            disk_used = usage.used
            disk_percent = (disk_used / disk_total) * 100 if disk_total else None
        except Exception:
            disk_total = disk_used = disk_percent = None

        cpu_percent = None

    ws_latency = None
    try:
        ws_latency = getattr(bot, "latency", None)
    except Exception:
        ws_latency = None

    os_info = platform.platform()
    python_version = platform.python_version()

    error_conditions = []
    warn_conditions = []

    if ram_percent is not None:
        if ram_percent >= 90:
            error_conditions.append(f"ram {ram_percent:.0f}%")
        elif ram_percent >= 75:
            warn_conditions.append(f"ram {ram_percent:.0f}%")

    if cpu_percent is not None:
        if cpu_percent >= 95:
            error_conditions.append(f"cpu {cpu_percent:.0f}%")
        elif cpu_percent >= 80:
            warn_conditions.append(f"cpu {cpu_percent:.0f}%")

    if disk_percent is not None:
        if disk_percent >= 95:
            error_conditions.append(f"disk {disk_percent:.0f}%")
        elif disk_percent >= 85:
            warn_conditions.append(f"disk {disk_percent:.0f}%")

    if ws_latency is not None:
        if ws_latency >= 3.0:
            error_conditions.append(f"ws {ws_latency:.2f}s")
        elif ws_latency >= 1.5:
            warn_conditions.append(f"ws {ws_latency:.2f}s")

    if error_conditions:
        state = "ERROR"
    elif warn_conditions:
        state = "REQUIRES ATTENTION"
    else:
        state = "OK"

    return {
        "ram_used": ram_used,
        "ram_available": ram_available,
        "ram_total": ram_total,
        "ram_percent": ram_percent,
        "cpu_percent": cpu_percent,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "disk_percent": disk_percent,
        "ws_latency": ws_latency,
        "os": os_info,
        "python_version": python_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "error_conditions": error_conditions,
        "warn_conditions": warn_conditions,
    }


def _make_status_embed(status: dict) -> discord.Embed:
    title = f"System status · {status.get('state', 'UNKNOWN')}"
    state = status.get("state", "UNKNOWN")
    color = discord.Color.green() if state == "OK" else discord.Color.orange() if state == "REQUIRES ATTENTION" else discord.Color.red()
    ts = status.get("timestamp")
    timestamp = datetime.fromisoformat(ts) if ts else None
    emb = discord.Embed(title=title, color=color, timestamp=timestamp)

    if status.get("ram_total") is not None:
        emb.add_field(
            name="RAM",
            value=f"Used: {_format_bytes(status.get('ram_used'))} / {_format_bytes(status.get('ram_total'))}\nAvailable: {_format_bytes(status.get('ram_available'))}\n{status.get('ram_percent'):.1f}% used" if status.get("ram_percent") is not None else "Unavailable",
            inline=False
        )
    else:
        emb.add_field(name="RAM", value="Unavailable (psutil not installed)", inline=False)

    cpu_val = f"{status['cpu_percent']:.1f}% used" if status.get("cpu_percent") is not None else "Unavailable"
    emb.add_field(name="CPU", value=cpu_val, inline=True)

    if status.get("disk_total") is not None:
        emb.add_field(
            name="Disk",
            value=f"Used: {_format_bytes(status.get('disk_used'))} / {_format_bytes(status.get('disk_total'))}\n{status.get('disk_percent'):.1f}% used" if status.get("disk_percent") is not None else "Unavailable",
            inline=True
        )
    else:
        emb.add_field(name="Disk", value="Unavailable", inline=True)

    if status.get("ws_latency") is not None:
        emb.add_field(name="Gateway latency", value=f"{status['ws_latency']*1000:.0f} ms", inline=True)
    else:
        emb.add_field(name="Gateway latency", value="Unavailable", inline=True)

    emb.add_field(name="OS", value=status.get("os", "Unknown"), inline=False)
    emb.add_field(name="Python", value=status.get("python_version", "Unknown"), inline=True)

    if status.get("error_conditions"):
        emb.add_field(name="Errors", value=", ".join(status["error_conditions"]), inline=False)
    elif status.get("warn_conditions"):
        emb.add_field(name="Warnings", value=", ".join(status["warn_conditions"]), inline=False)

    emb.set_footer(text="Watchdog")
    return emb


async def _cancel_all_other_tasks(timeout: float = 5.0) -> None:
    current = asyncio.current_task()
    tasks = [t for t in asyncio.all_tasks() if t is not current]
    if not tasks:
        return

    for t in tasks:
        t.cancel()

    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass


async def start_monitoring(bot: discord.Client, alert_channel_id: Optional[int] = None, interval: Optional[int] = None) -> None:
    try:
        if alert_channel_id is None:
            raw = get_value("LOG_ID")
            alert_channel_id = int(raw) if raw is not None else None
    except Exception:
        alert_channel_id = None

    try:
        if interval is None:
            interval = int(get_value("watchdog", "check_interval"))
    except Exception:
        interval = 30

    await bot.wait_until_ready()
    chan = bot.get_channel(alert_channel_id) if alert_channel_id else None

    while not bot.is_closed():
        try:
            status = await collect_status(bot)
            if status["state"] != "OK":
                emb = _make_status_embed(status)
                if chan:
                    try:
                        await chan.send(embed=emb)
                        try:
                            asyncio.create_task(log_to_channel(chan.guild, f"Watchdog detected {status['state']}", discord.Color.orange() if status["state"] != "ERROR" else discord.Color.red(), "watchdog"))
                        except Exception:
                            pass
                    except Exception as e:
                        try:
                            asyncio.create_task(log_to_channel(chan.guild, f"❌ Watchdog alert failed to send to channel: {e}", discord.Color.red(), "fail"))
                        except Exception:
                            print("Watchdog alert failed and logging failed:", e)
                else:
                    if bot.guilds:
                        guild = bot.guilds[0]
                        try:
                            asyncio.create_task(log_to_channel(guild, f"Watchdog detected {status['state']}: {', '.join(status['error_conditions'] or status['warn_conditions'])}", discord.Color.orange() if status["state"] != "ERROR" else discord.Color.red(), "watchdog"))
                        except Exception:
                            print(f"Watchdog detected {status['state']}: {status['error_conditions'] or status['warn_conditions']}")
                    else:
                        print(f"Watchdog detected {status['state']}: {status['error_conditions'] or status['warn_conditions']}")
        except Exception as e:
            if chan and chan.guild:
                try:
                    asyncio.create_task(log_to_channel(chan.guild, f"❌ Watchdog monitor exception: {e}", discord.Color.red(), "fail"))
                except Exception:
                    print("Watchdog monitor exception and logging failed:", e)
            elif bot.guilds:
                try:
                    asyncio.create_task(log_to_channel(bot.guilds[0], f"❌ Watchdog monitor exception: {e}", discord.Color.red(), "fail"))
                except Exception:
                    print("Watchdog monitor exception and logging failed:", e)
            else:
                print("Watchdog monitor exception:", e)

        await asyncio.sleep(interval)


class WatchdogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _user_is_manager(self, member: discord.Member) -> bool:
        try:
            if await self.bot.is_owner(member):
                return True
        except Exception:
            pass

        try:
            raw = get_value("roles", "bot_manager_ID")
            if raw is None:
                return False
            manager_id = int(raw)
        except Exception:
            return False

        return any(r.id == manager_id for r in member.roles)

    @commands.command(name="status")
    async def status(self, ctx: commands.Context):
        status = await collect_status(self.bot)
        emb = _make_status_embed(status)
        await ctx.send(embed=emb)

    @commands.command(name="reboot")
    @commands.has_role(get_value("roles", "bot_manager_ID"))
    async def reboot(self, ctx):
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        up_dir = os.path.abspath(os.path.join(cur_dir, ".."))
        root_dir = os.path.abspath(os.path.join(up_dir, ".."))
        #print(root_dir)

        subprocess.run(["python3", "-m", "PerfectionBot.scripts.reboot"], cwd=root_dir)

async def setup(bot: commands.Bot):
    await bot.add_cog(WatchdogCog(bot))