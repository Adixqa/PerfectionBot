#filter

import json
import re
import unicodedata
from rapidfuzz import fuzz
from pathlib import Path
from PerfectionBot.config.yamlHandler import get_value

CONFIG_PATH = Path(__file__).parents[1] / "config" / "banned-keywords.json"

def load_blacklist() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r'[^a-z0-9]', '', text)

blacklist = load_blacklist()
blacklist_normalized = {normalize(k): v for k, v in blacklist.items()}

def check_bad(message: str, threshold: int = get_value("behaviour", "filter", "DETECTION_THRESHOLD")) -> dict | None:
    nm = normalize(message)
    for nb, data in blacklist_normalized.items():
        if nb in nm:
            return {"word": nb, **data}
    for nb, data in blacklist_normalized.items():
        if fuzz.partial_ratio(nm, nb) >= threshold:
            return {"word": nb, **data}
    return None