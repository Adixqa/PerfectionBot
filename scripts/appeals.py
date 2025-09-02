#appeals

import json
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

APPEALS_PATH = DATA_DIR / "appeals.json"
print(APPEALS_PATH.resolve())

appeals: dict[str, dict] = {}

def save_appeals():
    try:
        APPEALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with APPEALS_PATH.open("w", encoding="utf-8") as f:
            json.dump(appeals, f, indent=2)
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