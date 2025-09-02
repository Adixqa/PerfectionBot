#filter

import re
import unicodedata
from pathlib import Path
from itertools import combinations
from rapidfuzz import fuzz, distance
from wordfreq import zipf_frequency
import spacy
from PerfectionBot.config.yamlHandler import get_value

CONFIG_PATH = Path(__file__).parents[1] / "config" / "banned-keywords.config"
SAFE_SUBSTRINGS = ["pass", "classic", "assignment", "class", "glass", "nagger", "dagger", "cam", "come", "where", "ore", "hoe", "grape", "whose", "who"]

nlp = spacy.load("en_core_web_sm")

def load_blacklist() -> list[str]:
    words = []
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            words.append(line)
    return words

def leet_replace(text: str) -> str:
    subs = {
        '1': 'i', '0': 'o', '3': 'e', '4': 'a', '5': 's',
        '7': 't', '@': 'a', '$': 's', '+': 't', '8': 'b', '!': 'i'
    }
    return ''.join(subs.get(c, c) for c in text.lower())

def normalize(text: str) -> str:
    text = leet_replace(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[^a-z0-9]+', ' ', text.lower())
    return text.strip()

def is_valid_word(word: str) -> bool:
    freq = zipf_frequency(word, "en")
    if freq > 0.0:
        return True
    common_suffixes = ["s", "es", "ed", "ing", "er", "ly"]
    for suffix in common_suffixes:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            base = word[:-len(suffix)]
            base_freq = zipf_frequency(base, "en")
            if base_freq > 0.0:
                return True
            if suffix in ["ed", "ing"] and len(base) > 2 and base[-1] == base[-2]:
                base2 = base[:-1]
                base2_freq = zipf_frequency(base2, "en")
                if base2_freq > 0.0:
                    return True
    return False

blacklist = load_blacklist()
blacklist_normalized = [normalize(w) for w in blacklist]

def check_bad(message: str, threshold: int = None, max_edits: int = 1) -> dict | None:
    if threshold is None:
        threshold = get_value("behaviour", "filter", "DETECTION_THRESHOLD")

    nm = normalize(message)
    doc = nlp(nm)
    tokens = [t.lemma_ for t in doc if not t.is_stop]
    flagged_words = []

    for w in tokens:
        if w in blacklist_normalized:
            flagged_words.append(w)
            continue
        if is_valid_word(w) or len(w) <= 3:
            continue
        for nb in blacklist_normalized:
            score = fuzz.ratio(w, nb)
            if score >= threshold and distance.Levenshtein.distance(w, nb) <= max_edits:
                flagged_words.append(nb)

    for r in range(2, 4):
        for combo in combinations(tokens, r):
            combined = ''.join(combo)
            if any(safe in combined for safe in SAFE_SUBSTRINGS):
                continue
            for nb in blacklist_normalized:
                if len(nb) != len(combined):
                    continue
                if combined == nb:
                    flagged_words.append(nb)
                    continue
                if is_valid_word(combined) or len(combined) <= 3:
                    continue
                score = fuzz.ratio(combined, nb)
                if score >= threshold and distance.Levenshtein.distance(combined, nb) <= max_edits:
                    flagged_words.append(nb)

    if flagged_words:
        return {"word": flagged_words[0]}
    return None