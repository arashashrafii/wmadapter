from __future__ import annotations

from typing import Any


class ToolCallRecovery:
    """Provider-neutral boundary for repairing incomplete WebChat turns.

    The current implementation remains in ``protocol.py`` for a safe staged
    migration. Keeping this facade separate lets the gateway and providers
    depend on a recovery contract without importing client-specific helpers.
    """

    async def resolve(
        self,
        provider: Any,
        answer: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        conversation_id: str | None,
        prompt: str,
    ) -> tuple[dict[str, Any] | None, str]:
        from .protocol import _resolve_web_answer

        return await _resolve_web_answer(
            provider, answer, messages, tools, conversation_id, prompt
        )
