import json
import re
import unicodedata
from rapidfuzz import fuzz, distance
from pathlib import Path
from wordfreq import zipf_frequency
from PerfectionBot.config.yamlHandler import get_value

CONFIG_PATH = Path(__file__).parents[1] / "config" / "banned-keywords.json"

def load_blacklist() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def leet_replace(text: str) -> str:
    subs = {
        '1': 'i',
        '0': 'o',
        '3': 'e',
        '4': 'a',
        '5': 's',
        '7': 't',
        '@': 'a',
        '$': 's',
        '+': 't',
        '8': 'b',
        '!': 'i'
    }
    return ''.join(subs.get(c, c) for c in text.lower())

def normalize(text: str) -> str:
    text = leet_replace(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[^a-z0-9]+', ' ', text.lower())
    return text.strip()

def is_valid_word(word: str) -> bool:
    return zipf_frequency(word, "en") > 1.0

blacklist = load_blacklist()
blacklist_normalized = {normalize(k): v for k, v in blacklist.items()}

def check_bad(message: str, threshold: int = None, max_edits: int = 1) -> dict | None:
    if threshold is None:
        threshold = get_value("behaviour", "filter", "DETECTION_THRESHOLD")

    nm = normalize(message)
    words = nm.split()

    for w in words:
        for nb, data in blacklist_normalized.items():
            if nb == w:
                return {"word": nb, **data}
            
            if is_valid_word(w):
                continue

            score = fuzz.ratio(w, nb)
            if score >= threshold:
                edit_dist = distance.Levenshtein.distance(w, nb)
                if edit_dist <= max_edits:
                    return {"word": nb, "score": score, "edit_distance": edit_dist, **data}

    joined = ''.join(words)
    max_len = max(len(nb) for nb in blacklist_normalized)

    for length in range(1, max_len + 1):
        for i in range(len(joined) - length + 1):
            substr = joined[i:i+length]
            for nb, data in blacklist_normalized.items():
                if len(nb) != length:
                    continue
                if substr == nb:
                    return {"word": nb, **data}
                
                if is_valid_word(substr):
                    continue

                score = fuzz.ratio(substr, nb)
                if score >= threshold:
                    edit_dist = distance.Levenshtein.distance(substr, nb)
                    if edit_dist <= max_edits:
                        return {"word": nb, "score": score, "edit_distance": edit_dist, **data}

    return None