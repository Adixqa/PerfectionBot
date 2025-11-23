# PerfectionBot/scripts/leveling.py

from pathlib import Path
import os
from PerfectionBot.config.yamlHandler import get_value

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

FILE = DATA_DIR / "xp.dat"
ROLE_CONF = CONFIG_DIR / "lvl.config"

BASE_XP = int(get_value("LEVELING", "BASE_XP"))
SCALE_FACTOR = float(get_value("LEVELING", "SCALE_FACTOR"))
MAX_LEVEL = 1000

XP_INCREMENTS = [20, 35, 40]
XP_EXTRA_STEP = 20

def ensure_file():
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.touch(exist_ok=True)

def read_xp(id: int) -> int:
    ensure_file()
    with FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            parts = line.split(":")
            if parts[0] == str(id):
                try:
                    return int(parts[1])
                except ValueError:
                    return 0
    return 0

def write_xp(id: int, value: int):
    ensure_file()
    lines = []
    found = False

    with FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            parts = line.split(":")
            if parts[0] == str(id):
                try:
                    current = int(parts[1])
                except ValueError:
                    current = 0
                new_value = current + value
                lines.append(f"{id}:{new_value}")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{id}:{value}")

    with FILE.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def convertToLevel(xp: int) -> int:
    if xp < 0:
        return 0

    level = 0
    remaining_xp = xp

    for inc in XP_INCREMENTS:
        if remaining_xp >= inc:
            remaining_xp -= inc
            level += 1
        else:
            return level

    while level < MAX_LEVEL:
        next_inc = XP_INCREMENTS[-1] + XP_EXTRA_STEP * (level - len(XP_INCREMENTS) + 1)
        if remaining_xp >= next_inc:
            remaining_xp -= next_inc
            level += 1
        else:
            break

    return min(level, MAX_LEVEL)

def read_level_roles():
    if not ROLE_CONF.exists():
        return []

    roles = []
    with ROLE_CONF.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            try:
                lvl_str, role_str = line.split(":", 1)
                lvl = int(lvl_str.strip())
                role_id = int(role_str.strip())
                roles.append((lvl, role_id))
            except ValueError:
                continue

    return sorted(roles, key=lambda x: x[0])

async def check_level_reward(member, new_level: int):
    roles = read_level_roles()
    if not roles:
        return None

    reward_role_id = None
    for lvl, role_id in roles:
        if new_level >= lvl:
            reward_role_id = role_id
        else:
            break

    if not reward_role_id:
        return None

    reward_role_ids = [role_id for _, role_id in roles]
    to_remove = [r for r in member.roles if r.id in reward_role_ids and r.id != reward_role_id]

    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason=f"Leveling system cleanup (level {new_level})")
    except Exception as e:
        print(f"[Leveling] Failed to remove old roles from {member}: {e}")

    role = member.guild.get_role(reward_role_id)
    if role and role not in member.roles:
        try:
            await member.add_roles(role, reason=f"Reached level {new_level}")
            return role
        except Exception as e:
            print(f"[Leveling] Failed to give role {reward_role_id} to {member}: {e}")
            return None

    return None