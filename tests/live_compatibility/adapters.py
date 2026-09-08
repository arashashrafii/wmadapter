from __future__ import annotations

import importlib.util
import shutil


def adapter_status(name: str) -> tuple[str, str]:
    available = importlib.util.find_spec("openai") is not None if name == "openai" else shutil.which("openclaw") is not None
    if not available:
        return "BLOCKED", f"{name} adapter is unavailable"
    return "available", "adapter detected"
