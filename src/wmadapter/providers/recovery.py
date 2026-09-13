from __future__ import annotations

from collections.abc import Callable
import hashlib
import logging
import uuid
from typing import Any

from .normalizer import ToolProtocolNormalizer
from .protocol import ToolProtocolStatus

logger = logging.getLogger(__name__)
_REPAIR_CONTEXT_LIMIT = 12000


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
    ) -> tuple[dict[str, Any] | None, str]:
        normalizer = ToolProtocolNormalizer()
        status, call, visible = normalizer.normalize_with_status(answer, tools)
        if call is not None and self.validate_call(call):
            _diagnostic("initial_valid", answer, "bypass")
            return call, visible

        # Ordinary content is already a complete provider response. Only ask
        # for repair when the WebChat leaked a protocol marker or returned an
        # empty turn; this avoids changing normal answers.
        needs_repair = status is ToolProtocolStatus.UNRESOLVED_MARKER or not visible.strip()
        if not needs_repair:
            _diagnostic("initial_complete", answer, "bypass")
            return None, visible

        if not visible.strip():
            _diagnostic("initial_empty", answer, "repair_requested")
        else:
            _diagnostic("initial_unresolved_marker", answer, "repair_requested")

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
            repaired = await provider.complete(repair_prompt, conversation_id=repair_id)
        else:
            logger.info(
                "protocol_recovery_context original=%s isolation=provider_api",
                _conversation_fingerprint(conversation_id),
            )
            repaired = await provider.repair_complete(
                repair_prompt, conversation_id=conversation_id
            )
        status, call, visible = normalizer.normalize_with_status(repaired, tools)
        if call is not None and not self.validate_call(call):
            _diagnostic("repair_invalid_tool_call", repaired, "fail_closed")
            call, visible = None, ""
        elif call is not None:
            _diagnostic("repair_valid_tool_call", repaired, "repaired_tool")
        elif not visible.strip():
            _diagnostic("repair_empty", repaired, "fail_closed")
        elif status is ToolProtocolStatus.UNRESOLVED_MARKER:
            _diagnostic("repair_unresolved_marker", repaired, "fail_closed")
        else:
            _diagnostic("repair_complete", repaired, "repaired_final")
        if call is None and (not visible.strip() or (tools and "<tool_call>" in repaired)):
            raise ProtocolRecoveryError("Web model failed to produce a valid response after one repair")
        return call, visible
