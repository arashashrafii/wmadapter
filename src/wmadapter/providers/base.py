from __future__ import annotations

from abc import ABC, abstractmethod
from .contract import ModelCapabilities, ProviderRequest, ProviderResult
from .webchat_adapter import WebChatTextAdapter
from collections.abc import AsyncIterator
import hashlib


class ChatProvider(ABC):
    name: str
    model_ids: tuple[str, ...] = ()
    capabilities = ModelCapabilities()
    protocol = WebChatTextAdapter()
    context_budget_chars: int | None = None
    context_budget_profiles: dict[str, int] = {}

    def model_capabilities(self, model: str) -> dict[str, bool]:
        """Capabilities for one discovered model, kept provider-owned."""
        return {
            "chat": True,
            "image_generation": bool(self.capabilities.image_generation),
        }

    def context_budget_for(self, model: str) -> int | None:
        return self.context_budget_profiles.get(
            model, self.context_budget_profiles.get(self.name, self.context_budget_chars)
        )

    async def infer(self, request: ProviderRequest) -> ProviderResult:
        """V2 entrypoint; existing V1 subclasses need no new methods."""
        from .legacy import infer_legacy
        return await infer_legacy(self, request)

    async def stream_infer(self, request: ProviderRequest) -> AsyncIterator[ProviderResult]:
        """Buffered normalized result, not token deltas."""
        yield await self.infer(request)


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

    async def repair_complete(self, prompt: str, conversation_id: str | None = None) -> str:
        """Complete repair text without appending it to primary history."""
        seed = conversation_id or "anonymous"
        repair_id = "repair:" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:24]
        return await self.complete(prompt, conversation_id=repair_id)

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Release provider-side state for one logical conversation."""
        return False

    async def stream_complete(
        self, prompt: str, conversation_id: str | None = None
    ) -> AsyncIterator[str]:
        yield await self.complete(prompt, conversation_id=conversation_id)
