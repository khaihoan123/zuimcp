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
    if _override is not None:
        config[_key] = int(_override) if isinstance(_default, int) else _override

LOCAL_EMAIL = os.environ.get("EMAIL_ADDRESS")
LOCAL_PASS = os.environ.get("EMAIL_PASSWORD")
