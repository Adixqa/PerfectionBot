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

blacklist = load_blacklist()

def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r'[^a-z0-9]', '', text)

def check_bad(message: str, threshold: int = get_value("behaviour", "filter", "DETECTION_THRESHOLD")) -> dict | None:
    nm = normalize(message)
    for bad_word, data in blacklist.items():
        nb = normalize(bad_word)
        if nb in nm:
            return {"word": bad_word, **data}
        if fuzz.partial_ratio(nm, nb) >= threshold:
            return {"word": bad_word, **data}
    return None