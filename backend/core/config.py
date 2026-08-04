import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_config_path = Path(__file__).resolve().parent.parent / "config.yaml"
with open(_config_path) as f:
    config = yaml.safe_load(f) or {}

LOCAL_EMAIL = os.environ.get("EMAIL_ADDRESS")
LOCAL_PASS = os.environ.get("EMAIL_PASSWORD")