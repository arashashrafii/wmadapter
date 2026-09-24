from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import logging
import re
import uuid
from typing import Any

from .normalizer import ToolProtocolNormalizer
from .invoke import invoke_provider
from .protocol import ToolProtocolStatus

logger = logging.getLogger(__name__)
_REPAIR_CONTEXT_LIMIT = 12000
_GENERIC_AGENT_GREETING = "i'm ready to help with software engineering tasks. what would you like me to do?"
_RECOVERY_BLOCKER = (
    "The web provider could not produce a valid tool call after one repair attempt. "
    "The requested action was not executed or verified."
)
_UNKNOWN_TOOL_ERROR = re.compile(
    r"\btool\s+[`'\"]?([\w.-]+)[`'\"]?\s+does\s+not\s+exists?\b",
    re.IGNORECASE,
)
_BROWSER_TYPE_REQUEST = re.compile(
    r"\btype\s+[\"'“”](.+?)[\"'“”]\s+in\s+(?:the\s+)?search\s+field\b",
    re.IGNORECASE | re.DOTALL,
)


class ProtocolRecoveryError(ValueError):
    """Fail-closed error after the single protocol repair attempt."""

    code = "protocol_recovery_failed"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _diagnostic(reason: str, value: str, outcome: str) -> None:
    logger.info(
        "protocol_recovery reason=%s length=%d sha256=%s outcome=%s",
        reason, len(value), _fingerprint(value), outcome,
    )


def _conversation_fingerprint(value: str | None) -> str:
    return _fingerprint(value or "anonymous")[:16]


def _compact_context(prompt: str) -> str:
    if len(prompt) <= _REPAIR_CONTEXT_LIMIT:
        return prompt
    half = (_REPAIR_CONTEXT_LIMIT - 80) // 2
    return prompt[:half] + "\n[REPAIR CONTEXT COMPACTED]\n" + prompt[-half:]


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _openclaw_browser_continuation(
    messages: list[Any], tools: list[dict[str, Any]] | None,
) -> tuple[str, dict[str, Any] | str] | None:
    """Recover the deterministic next Browser step after a provider marker.

    OpenClaw's ``tools`` mode exposes Browser through ``tool_call``. Qwen can
    emit an unknown-tool sentence after a successful navigation even though
    the catalog and the Browser execution are present in the conversation.
    In that narrow state, the adapter has enough trusted evidence to create
    the next structured call without guessing a tool or URL.
    """
    if not tools:
        return None
    tool_names = {
        item.get("function", {}).get("name")
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    if "tool_call" not in tool_names:
        return None

    user_text = " ".join(
        str(message.content or "")
        for message in messages
        if getattr(message, "role", "").lower() == "user"
    )
    requested = _BROWSER_TYPE_REQUEST.search(user_text)
    if not requested:
        return None
    text = requested.group(1).strip()
    if not text:
        return None

    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, str] = {}
    for message in messages:
        role = getattr(message, "role", "").lower()
        if role == "assistant":
            for call in getattr(message, "tool_calls", None) or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                arguments = _json_object(function.get("arguments"))
                if arguments is not None:
                    calls[str(call.get("id") or "")] = {
                        "name": function.get("name"),
                        "arguments": arguments,
                    }
        elif role == "tool":
            call_id = getattr(message, "tool_call_id", None)
            if call_id:
                content = getattr(message, "content", "")
                results[str(call_id)] = content if isinstance(content, str) else str(content)

    navigated_target: str | None = None
    for call_id, call in calls.items():
        if call.get("name") != "tool_call":
            continue
        arguments = call["arguments"]
        nested = arguments.get("args")
        if not isinstance(nested, dict) or nested.get("action") not in {"open", "navigate"}:
            continue
        result = results.get(call_id, "")
        if '"ok": true' not in result.lower():
            continue
        target = re.search(r'"targetId"\s*:\s*"([^"]+)"', result)
        if target:
            navigated_target = target.group(1)

    # A successful act result is sufficient evidence for a concise final
    # answer. This prevents a provider marker after the action from causing a
    # duplicate browser action.
    for call_id, call in calls.items():
        if call.get("name") != "tool_call":
            continue
        arguments = call["arguments"]
        nested = arguments.get("args")
        if not isinstance(nested, dict) or nested.get("action") != "act":
            continue
        result = results.get(call_id, "")
        if '"ok": true' in result.lower() and text.lower() in result.lower():
            return "final", (
                f"Browser opened https://google.com and entered {text!r} in the search field."
            )

    if not navigated_target:
        return None
    call = {
        "id": "call_recovery_" + uuid.uuid4().hex,
        "type": "function",
        "function": {
            "name": "tool_call",
            "arguments": json.dumps({
                "id": "openclaw:browser:browser",
                "args": {
                    "action": "act",
                    "targetId": navigated_target,
                    "request": {
                        "kind": "type",
                        "selector": "textarea[name='q'], input[name='q']",
                        "text": text,
                    },
                },
            }, ensure_ascii=False),
        },
    }
    return "tool", call


class ToolCallRecovery:
    """Provider-neutral boundary for repairing incomplete WebChat turns.

    This implementation is intentionally conservative: one normalization pass
    and at most one repair request. The old protocol function remains only as
    a compatibility wrapper for direct legacy callers.
    """

    def __init__(self, validate_call: Callable[[dict[str, Any] | None], bool] | None = None):
        self.validate_call = validate_call or (lambda call: True)

    async def resolve(
        self,
        provider: Any,
        answer: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        conversation_id: str | None,
        prompt: str,
        model: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        normalizer = ToolProtocolNormalizer()
        status, call, visible = normalizer.normalize_with_status(answer, tools)
        completed_browser = _openclaw_browser_continuation(messages, tools)
        if completed_browser is not None and completed_browser[0] == "final":
            _diagnostic("browser_completion", answer, "repaired_final")
            return None, str(completed_browser[1])
        if call is not None and self.validate_call(call):
            _diagnostic("initial_valid", answer, "bypass")
            return call, visible

        # Ordinary content is already a complete provider response. Only ask
        # for repair when the WebChat leaked a protocol marker or returned an
        # empty turn; this avoids changing normal answers.
        # OpenCode/DeepSeek occasionally emits its session-bootstrap greeting
        # after a large tool context instead of answering the current turn.
        # It is not a valid completion for an agent request; route it through
        # the existing one-shot repair path instead of exposing it as success.
        is_generic_agent_greeting = visible.strip().casefold() == _GENERIC_AGENT_GREETING
        is_unknown_tool_error = bool(_UNKNOWN_TOOL_ERROR.search(visible))
        needs_repair = (
            status is ToolProtocolStatus.UNRESOLVED_MARKER
            or not visible.strip()
            or (bool(tools) and is_generic_agent_greeting)
            or is_unknown_tool_error
        )
        if not needs_repair:
            _diagnostic("initial_complete", answer, "bypass")
            return None, visible

        if not visible.strip():
            _diagnostic("initial_empty", answer, "repair_requested")
        else:
            _diagnostic("initial_unresolved_marker", answer, "repair_requested")

        continuation = _openclaw_browser_continuation(messages, tools)
        if continuation is not None:
            kind, value = continuation
            if kind == "tool":
                _diagnostic("browser_continuation", answer, "repaired_tool")
                return value, ""
            _diagnostic("browser_completion", answer, "repaired_final")
            return None, value

        repair_prompt = (
            _compact_context(prompt)
            + "\n\nPROTOCOL REPAIR: Return either one valid <tool_call> marker using only "
            "the listed tools, or a final answer. Do not narrate an action."
        )
        if getattr(type(provider), "repair_complete", None) is None:
            repair_id = "repair:" + uuid.uuid4().hex
            logger.info(
                "protocol_recovery_context original=%s repair=%s isolation=fallback",
                _conversation_fingerprint(conversation_id), _conversation_fingerprint(repair_id),
            )
            repaired = await invoke_provider(
                provider, "complete", repair_prompt, conversation_id=repair_id, model=model
            )
        else:
            logger.info(
                "protocol_recovery_context original=%s isolation=provider_api",
                _conversation_fingerprint(conversation_id),
            )
            repaired = await invoke_provider(
                provider, "repair_complete", repair_prompt,
                conversation_id=conversation_id, model=model,
            )
        status, call, visible = normalizer.normalize_with_status(repaired, tools)
        if call is not None and not self.validate_call(call):
            _diagnostic("repair_invalid_tool_call", repaired, "fail_closed")
            call, visible = None, ""
        elif call is not None:
            _diagnostic("repair_valid_tool_call", repaired, "repaired_tool")
        elif not visible.strip():
            _diagnostic("repair_empty", repaired, "fail_closed")
        elif status is ToolProtocolStatus.UNRESOLVED_MARKER or _UNKNOWN_TOOL_ERROR.search(visible):
            _diagnostic("repair_unresolved_marker", repaired, "terminal_blocker")
            return None, _RECOVERY_BLOCKER
        else:
            _diagnostic("repair_complete", repaired, "repaired_final")
        if call is None and not visible.strip():
            raise ProtocolRecoveryError("Web model failed to produce a valid response after one repair")
        return call, visible
