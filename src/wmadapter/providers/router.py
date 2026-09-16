from __future__ import annotations

from .base import ChatProvider


class ProviderRouter:
    def __init__(self, providers: dict[str, ChatProvider], default_provider: str,
                 enabled_providers: list[str] | tuple[str, ...] | None = None,
                 enabled_models: list[str] | tuple[str, ...] | None = None):
        if default_provider not in providers:
            raise RuntimeError(f"Default provider {default_provider!r} is not configured")
        self.providers = providers
        self.default_provider = default_provider
        self.enabled_providers = tuple(
            (default_provider,) if enabled_providers is None else enabled_providers
        )
        self.enabled_models = None if enabled_models is None else frozenset(enabled_models)
        unknown = set(self.enabled_providers) - set(providers)
        if unknown:
            raise RuntimeError(f"Enabled provider is not configured: {sorted(unknown)[0]!r}")

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

    def resolve_model(self, model: str) -> ChatProvider:
        """Strict public model resolution; provider_for_model is legacy."""
        plain = model.split(":", 1)[-1]
        for provider_name in self.enabled_providers:
            provider = self.providers[provider_name]
            if plain in getattr(provider, "model_ids", ()) and self.model_enabled(plain):
                return provider
        raise RuntimeError(f"Unknown model {model!r}")

    def model_enabled(self, model: str) -> bool:
        return self.enabled_models is None or model in self.enabled_models

    def models_for_provider(self, name: str) -> tuple[str, ...]:
        provider = self.providers[name]
        return tuple(model for model in provider.model_ids if self.model_enabled(model))

    async def start(self) -> None:
        failures = []
        errors = {}
        for name in self.enabled_providers:
            provider = self.providers[name]
            try:
                await provider.start()
            except Exception as exc:
                provider.ready = False
                provider.last_error = "provider_startup_failed"
                failures.append(name)
                errors[name] = str(exc).strip() or exc.__class__.__name__
        if failures:
            details = ", ".join(
                f"{name}: {errors[name]}"
                for name in failures
            )
            raise RuntimeError(f"Enabled provider startup failed ({details}); retry after fixing configuration")

    async def stop(self) -> None:
        for name in self.enabled_providers:
            await self.providers[name].stop()

    async def status(self) -> dict:
        return {name: await provider.status() for name, provider in self.providers.items()}
