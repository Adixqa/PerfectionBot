from pathlib import Path
import os

from PerfectionBot.config.yamlHandler import get_value

FILE = Path(__file__).parents[1] / "data" / "xp.dat"

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
    with open(FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) != 2:
                continue
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
    with open(FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) != 2:
                lines.append(line)
                continue
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
    with open(FILE, "w") as f:
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