import os
import yaml

config_path = os.path.join(os.path.dirname(__file__), "conf.yml")

with open(config_path, "r") as file:
    _config = yaml.safe_load(file)

def get_value(*keys):
    """
    Access nested values in the YAML by providing keys in order.
    Example: get_value("game", "resolution", "width")
    """
    value = _config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise KeyError(f"Key path {' -> '.join(keys)} not found in config.")
    return value