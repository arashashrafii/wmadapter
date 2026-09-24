from __future__ import annotations

from abc import ABC, abstractmethod
from .contract import ModelCapabilities, ProviderRequest, ProviderResult
from .webchat_adapter import WebChatTextAdapter
from .invoke import invoke_provider
from collections.abc import AsyncIterator
import hashlib


class ChatProvider(ABC):
    name: str
    model_ids: tuple[str, ...] = ()
    capabilities = ModelCapabilities()
    protocol = WebChatTextAdapter()
    context_budget_chars: int | None = None
    context_budget_profiles: dict[str, int] = {}
    max_request_timeout_ms = 900000

    def request_timeout_ms(self, prompt: str) -> int:
        """Scale browser observation time for large provider turns.

        Web providers do not expose a stable first-token or DOM-rendering SLA.
        Keep the policy at the provider contract boundary so every adapter,
        including future web-chat providers, gets the same safe default.
        """
        base_timeout_ms = int(getattr(self, "timeout_ms", 180000))
        extra_chars = max(0, len(prompt) - 12000)
        extension = ((extra_chars + 7999) // 8000) * 30000
        return min(int(self.max_request_timeout_ms), base_timeout_ms + extension)

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
    async def complete(
        self, prompt: str, conversation_id: str | None = None, model: str | None = None
    ) -> str:
        pass

    async def repair_complete(
        self, prompt: str, conversation_id: str | None = None, model: str | None = None
    ) -> str:
        """Complete repair text without appending it to primary history."""
        seed = conversation_id or "anonymous"
        repair_id = "repair:" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:24]
        return await invoke_provider(
            self, "complete", prompt, conversation_id=repair_id, model=model
        )

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Release provider-side state for one logical conversation."""
        return False

    async def stream_complete(
        self, prompt: str, conversation_id: str | None = None, model: str | None = None
    ) -> AsyncIterator[str]:
        yield await invoke_provider(
            self, "complete", prompt, conversation_id=conversation_id, model=model
        )
