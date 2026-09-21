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
        provider_name, separator, plain = model.partition(":")
        if not separator:
            plain = model
            matches = [name for name in self.enabled_providers
                       if plain in getattr(self.providers[name], "model_ids", ())
                       and self.model_enabled(plain)]
            if len(matches) == 1:
                return self.providers[matches[0]]
            if len(matches) > 1:
                raise RuntimeError(f"Ambiguous model {model!r}; use provider:model")
        elif provider_name in self.enabled_providers:
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

    def model_catalog(self) -> list[dict]:
        """Build a stable public catalog while keeping provider failures isolated."""
        catalog = []
        for name in self.enabled_providers:
            provider = self.providers[name]
            ready = bool(getattr(provider, "ready", False))
            # status is async for real providers; the synchronous attribute is
            # intentionally the source for discovery, so catalog construction
            # never performs I/O or blocks on a browser.
            models = self.models_for_provider(name)
            default_model = models[0] if models else None
            for model in models:
                capabilities = provider.model_capabilities(model)
                catalog.append({
                    "id": model,
                    "model": model,
                    "provider": name,
                    "capability": capabilities,
                    "capabilities": provider.capabilities.model_dump(),
                    "readiness": ready,
                    "ready": ready,
                    "default": name == self.default_provider and model == default_model,
                })
        return catalog

    async def stop(self) -> None:
        for name in self.enabled_providers:
            await self.providers[name].stop()

    async def status(self) -> dict:
        return {name: await provider.status() for name, provider in self.providers.items()}
