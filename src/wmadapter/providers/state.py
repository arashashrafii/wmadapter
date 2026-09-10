"""Ephemeral gateway-local continuation state.

The registry deliberately stores identifier relationships only. Provider
credentials, messages, and raw transcripts remain outside this state boundary.
"""
from __future__ import annotations

from .contract import ConversationId, ResponseId


class UnknownResponseId(LookupError):
    """Raised when a continuation references an unknown or expired response."""


class ConversationStateConflict(ValueError):
    """Raised when identifiers point at different gateway-local conversations."""


class GatewayState:
    """Process-local response-to-conversation mapping.

    State is intentionally non-persistent: clearing it models a gateway
    restart and invalidates all response continuations.
    """

    def __init__(self) -> None:
        self._response_conversations: dict[ResponseId, ConversationId] = {}

    def remember(self, response_id: ResponseId, conversation_id: ConversationId) -> None:
        """Associate an opaque response identifier with its conversation."""
        self._response_conversations[response_id] = conversation_id

    def resolve(
        self,
        conversation_id: ConversationId | None = None,
        previous_response_id: ResponseId | None = None,
    ) -> ConversationId | None:
        """Resolve explicit continuation identifiers without inspecting values."""
        if previous_response_id is None:
            return conversation_id

        mapped = self._response_conversations.get(previous_response_id)
        if mapped is None:
            raise UnknownResponseId(previous_response_id)
        if conversation_id is not None and conversation_id != mapped:
            raise ConversationStateConflict(
                "conversation_id and previous_response_id refer to different conversations"
            )
        return mapped

    def expire(self, response_id: ResponseId) -> bool:
        """Forget one response continuation, making it expired."""
        return self._response_conversations.pop(response_id, None) is not None

    def clear(self) -> None:
        """Drop all continuation state, as on process restart."""
        self._response_conversations.clear()

    def __len__(self) -> int:
        return len(self._response_conversations)
