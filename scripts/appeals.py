# PerfectionBot/scripts/appeals.py

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

APPEALS_PATH = DATA_DIR / "appeals.json"

appeals: dict[str, dict] = {}

def save_appeals():
    try:
        with APPEALS_PATH.open("w", encoding="utf-8") as f:
            json.dump(appeals, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save appeals.json: {e}")

def load_appeals():
    global appeals
    try:
        if APPEALS_PATH.exists():
            with APPEALS_PATH.open("r", encoding="utf-8") as f:
                appeals = json.load(f)
        else:
            appeals = {}
    except Exception as e:
        print(f"Failed to load appeals.json: {e}")
        appeals = {}