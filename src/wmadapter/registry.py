"""Provider and model discovery shared by the CLI and HTTP catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_PROVIDERS = ("deepseek", "qwen")
_DEEPSEEK_MODELS = ("deepseek-chat", "deepseek-reasoner")
_DEFAULT_QWEN_MODELS = ("qwen-chat",)


@dataclass(frozen=True)
class DiscoveredModel:
    provider: str
    model: str
    capabilities: dict[str, bool]


def discover_models(config: dict[str, Any], provider: str | None = None) -> tuple[DiscoveredModel, ...]:
    """Return deterministic, provider-owned model metadata without starting a browser."""
    names = (provider,) if provider is not None else SUPPORTED_PROVIDERS
    discovered: list[DiscoveredModel] = []
    for name in names:
        if name == "deepseek":
            models = _DEEPSEEK_MODELS
            image_generation = False
        elif name == "qwen":
            qwen = config.get("qwen", {})
            models = tuple(qwen.get("models") or _DEFAULT_QWEN_MODELS)
            image_generation = bool(qwen.get("image_generation_verified", False))
        else:
            raise ValueError(f"Unsupported provider: {name}")
        for model in models:
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"{name}.models must contain non-empty model IDs")
            discovered.append(DiscoveredModel(
                provider=name,
                model=model,
                capabilities={"chat": True, "image_generation": image_generation},
            ))
    return tuple(discovered)


def provider_names() -> tuple[str, ...]:
    return SUPPORTED_PROVIDERS
