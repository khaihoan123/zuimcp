import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_config_path = Path(__file__).resolve().parent.parent / "config.yaml"
with open(_config_path) as f:
    config = yaml.safe_load(f) or {}

# Environment variables override config.yaml, so one image runs anywhere:
# config.yaml holds the local-dev defaults, containers inject real values.
for _key, _default in config.items():
    _override = os.environ.get(_key)
    if _override is None:
        continue
    # bool before int: bool is a subclass of int, and int("true") would raise.
    if isinstance(_default, bool):
        config[_key] = _override.strip().lower() in ("1", "true", "yes")
    elif isinstance(_default, int):
        config[_key] = int(_override)
    else:
        config[_key] = _override

LOCAL_EMAIL = os.environ.get("EMAIL_ADDRESS")
LOCAL_PASS = os.environ.get("EMAIL_PASSWORD")
