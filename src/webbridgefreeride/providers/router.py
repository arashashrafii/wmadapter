from __future__ import annotations

from .base import ChatProvider


class ProviderRouter:
    def __init__(self, providers: dict[str, ChatProvider], default_provider: str):
        if default_provider not in providers:
            raise RuntimeError(f"Default provider {default_provider!r} is not configured")
        self.providers = providers
        self.default_provider = default_provider

    def provider_for_model(self, model: str | None) -> ChatProvider:
        if not model:
            return self.providers[self.default_provider]
        if ":" in model:
            provider_name = model.split(":", 1)[0]
        elif model.startswith("qwen"):
            provider_name = "qwen"
        elif model.startswith("deepseek"):
            provider_name = "deepseek"
        else:
            provider_name = self.default_provider
        if provider_name not in self.providers:
            raise RuntimeError(f"No provider configured for model {model!r}")
        return self.providers[provider_name]

    async def start(self) -> None:
        await self.providers[self.default_provider].start()

    async def stop(self) -> None:
        for provider in self.providers.values():
            await provider.stop()

    async def status(self) -> dict:
        return {name: await provider.status() for name, provider in self.providers.items()}
