from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8000},
    "browser": {"headless": False, "profile_dir": ".webbridge-profile"},
    "deepseek": {"chat_url": "https://chat.deepseek.com/", "timeout_ms": 180000},
    "logging": {"level": "INFO", "file": "webbridgefreeride.log"},
}


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    p = Path(path)
    if p.exists():
        user = yaml.safe_load(p.read_text()) or {}
        for section, values in user.items():
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
            else:
                cfg[section] = values
    return cfg
