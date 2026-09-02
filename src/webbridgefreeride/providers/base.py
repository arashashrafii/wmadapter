from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ChatProvider(ABC):
    name: str

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    @abstractmethod
    async def status(self) -> dict:
        pass

    @abstractmethod
    async def complete(self, prompt: str, conversation_id: str | None = None) -> str:
        pass

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Release provider-side state for one logical conversation."""
        return False

    async def stream_complete(
        self, prompt: str, conversation_id: str | None = None
    ) -> AsyncIterator[str]:
        yield await self.complete(prompt, conversation_id=conversation_id)
