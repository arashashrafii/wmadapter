from __future__ import annotations

from .base import ChatProvider


class ProviderRouter:
    def __init__(self, providers: dict[str, ChatProvider], default_provider: str | None = None,
                 enabled_providers: list[str] | tuple[str, ...] | None = None,
                 enabled_models: list[str] | tuple[str, ...] | None = None):
        self.providers = providers
        self.default_provider = default_provider if default_provider in providers else None
        self.enabled_providers = tuple(providers)
        self.enabled_models = None

    def provider_for_model(self, model: str | None) -> ChatProvider:
        if not model:
            for provider in self.providers.values():
                if getattr(provider, "ready", False):
                    return provider
            if self.default_provider:
                return self.providers[self.default_provider]
            return next(iter(self.providers.values()))
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

    def resolve_model(self, model: str) -> ChatProvider:
        """Strict public model resolution; provider_for_model is legacy."""
        plain = model.split(":", 1)[-1]
        for provider in self.providers.values():
            if plain in getattr(provider, "model_ids", ()):
                return provider
        raise RuntimeError(f"Unknown model {model!r}")

    def model_enabled(self, model: str) -> bool:
        return True

    def models_for_provider(self, name: str) -> tuple[str, ...]:
        return tuple(self.providers[name].model_ids)

    async def start(self) -> None:
        failures = []
        errors = {}
        for name, provider in self.providers.items():
            try:
                await provider.start()
            except Exception as exc:
                provider.ready = False
                provider.last_error = "provider_startup_failed"
                failures.append(name)
                errors[name] = str(exc).strip() or exc.__class__.__name__
        # Authentication is provider-local. An unauthenticated provider must
        # remain visible in status without preventing ready providers from
        # serving requests.

    async def stop(self) -> None:
        for name in self.providers:
            await self.providers[name].stop()

    async def status(self) -> dict:
        return {name: await provider.status() for name, provider in self.providers.items()}
