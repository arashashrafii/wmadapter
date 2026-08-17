from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SECRET_KEYS = ("password", "token", "secret", "cookie", "authorization", "credential")
SECRET_PATTERNS = [
    re.compile(r"(?i)(password|token|secret|cookie|authorization|credential)([\s:=]+)([^\s,;]+)"),
    re.compile(r"gho_[A-Za-z0-9_]+"),
]


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in SECRET_KEYS) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        text = value
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]" if len(match.groups()) >= 3 else "[REDACTED]", text)
        return text
    return value
