import os
import yaml

config_path = os.path.join(os.path.dirname(__file__), "conf.yml")

with open(config_path, "r", encoding="utf-8-sig") as file:
    _config = yaml.safe_load(file)

def _normalize(value):
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        val_lower = value.strip().lower()
        if val_lower == "true":
            return True
        if val_lower == "false":
            return False

        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass

    return value

def get_value(*keys):
    value = _config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise KeyError(f"Key path {' -> '.join(keys)} not found in config.")
    return _normalize(value)