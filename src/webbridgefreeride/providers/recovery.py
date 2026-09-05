from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .normalizer import ToolProtocolNormalizer


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
        call, visible = normalizer.normalize(answer, tools)
        if call is not None and self.validate_call(call):
            return call, visible

        # Ordinary content is already a complete provider response. Only ask
        # for repair when the WebChat leaked a protocol marker or returned an
        # empty turn; this avoids changing normal answers.
        needs_repair = not visible.strip() or (tools and "<tool_call>" in answer)
        if not needs_repair:
            return None, visible

        repair_prompt = (
            prompt
            + "\n\nPROTOCOL REPAIR: Return either one valid <tool_call> marker using only "
            "the listed tools, or a final answer. Do not narrate an action."
        )
        repaired = await provider.complete(
            repair_prompt, conversation_id=conversation_id
        )
        call, visible = normalizer.normalize(repaired, tools)
        if call is not None and not self.validate_call(call):
            call, visible = None, ""
        if call is None and (not visible.strip() or (tools and "<tool_call>" in repaired)):
            raise ValueError("Web model failed to produce a valid response after one repair")
        return call, visible
