from __future__ import annotations

from enum import StrEnum
from typing import Any


class ClientPolicy(StrEnum):
    """Prompt behavior selected by the consuming agent, not the web provider."""

    GENERIC = "generic"
    OPENCLAW = "openclaw"


def detect_client_policy(messages: list[Any], tools: list[dict[str, Any]] | None) -> ClientPolicy:
    """Detect the compatibility policy from the request context.

    This is intentionally conservative: generic OpenAI clients receive only
    the shared tool protocol. OpenClaw-specific workflow guidance is enabled
    only when its identifiers are present in the conversation.
    """
    if not tools:
        return ClientPolicy.GENERIC
    text = " ".join(
        str(getattr(message, "content", "") or "").lower()
        for message in messages
        if getattr(message, "role", "").lower() == "user"
    )
    markers = ("openclaw", "computer.act", "screen.snapshot", "openclaw plugin", "openclaw skill")
    return ClientPolicy.OPENCLAW if any(marker in text for marker in markers) else ClientPolicy.GENERIC
