from __future__ import annotations

from enum import StrEnum
from typing import Any


class ClientPolicy(StrEnum):
    """Prompt behavior selected by the consuming agent, not the web provider."""

    GENERIC = "generic"
    OPENCLAW = "openclaw"


def detect_client_policy(
    messages: list[Any],
    tools: list[dict[str, Any]] | None,
    client_hint: str | None = None,
) -> ClientPolicy:
    """Detect the compatibility policy from the request context.

    This is intentionally conservative: generic OpenAI clients receive only
    the shared tool protocol. OpenClaw-specific workflow guidance is enabled
    only when its identifiers are present in the conversation.
    """
    if isinstance(client_hint, str) and "openclaw" in client_hint.casefold():
        return ClientPolicy.OPENCLAW

    text = " ".join(
        str(getattr(message, "content", "") or "").lower()
        for message in messages
        if getattr(message, "role", "").lower() == "user"
    )
    markers = ("openclaw", "computer.act", "screen.snapshot", "openclaw plugin", "openclaw skill")
    if any(marker in text for marker in markers):
        return ClientPolicy.OPENCLAW
    tool_names = {
        item.get("function", {}).get("name", "").casefold()
        for item in tools or []
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    openclaw_tools = {"browser", "computer", "process", "session_status", "nodes"}
    return ClientPolicy.OPENCLAW if tool_names & openclaw_tools else ClientPolicy.GENERIC


_OPENCLAW_HEADINGS = (
    "OPENCLAW CAPABILITY POLICY:", "OPENCLAW DOCUMENTATION POLICY:",
    "RESEARCH-ACTION-VERIFICATION LOOP:", "OPENCLAW CAPABILITY CHECK:",
    "SELF-REMEDIATION:", "DESKTOP GUI POLICY:", "APPLICATION VERIFICATION EXAMPLE:",
    "PENDING TOOL RESULTS:", "MANDATORY FOLLOW-UP:", "STRUCTURED TOOL CALLS:",
    "LAUNCH ORDER:", "BROWSER TOOL SHAPE:", "SELF-CORRECTION WORKFLOW:",
)


def strip_openclaw_instructions(prompt: str) -> str:
    """Remove legacy client policy paragraphs from a generic request."""
    return "\n\n".join(
        part for part in prompt.split("\n\n")
        if not part.startswith(_OPENCLAW_HEADINGS)
    )
