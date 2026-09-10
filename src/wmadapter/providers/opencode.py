"""OpenCode-only request translation at the compatibility boundary."""
from __future__ import annotations

from typing import Any

from .contract import ChatRequest


def translate_request(payload: Any) -> ChatRequest:
    """Translate an OpenCode JSON request using the shared canonical contract."""
    if not isinstance(payload, dict):
        raise ValueError("OpenCode request must be a JSON object")
    if "messages" not in payload:
        raise ValueError("OpenCode request requires messages")
    try:
        return ChatRequest.model_validate(payload)
    except Exception as exc:
        raise ValueError("Invalid OpenCode chat request") from exc
