from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


def canonical_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def provider_profile_dir(provider: str) -> str:
    root = Path(os.getenv("WMADAPTER_DATA_DIR", "~/.local/share/wmadapter")).expanduser()
    return str(root / "profiles" / provider)


BUILTIN_PROVIDER_MODELS = {
    "deepseek": ("deepseek-chat", "deepseek-reasoner"),
    "qwen": (
        "qwen-chat",
        "qwen3.7-plus",
        "qwen3.8-max",
        "qwen3.8-omni-flash",
        "qwen-image-3.0",
    ),
}


def config_file_path(path: str | Path | None = None) -> Path:
    return Path(path or os.getenv("WMADAPTER_CONFIG", "config.yaml"))


def save_config(data: dict[str, Any], path: str | Path | None = None) -> Path:
    """Persist validated gateway configuration for the local CLI."""
    target = config_file_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def provider_profile_from_config(config: dict[str, Any], provider: str) -> str:
    if provider == "qwen":
        return config.get("qwen", {}).get("profile_dir", provider_profile_dir(provider))
    return config.get("browser", {}).get("profile_dir", provider_profile_dir(provider))


def update_provider_config(config: dict[str, Any], action: str, provider: str) -> dict[str, Any]:
    """Apply one safe provider lifecycle change without touching auth state."""
    if provider not in BUILTIN_PROVIDER_MODELS:
        raise ValueError(f"Unsupported provider: {provider}")
    updated = dict(config)
    providers = dict(updated.get("providers", {}))
    enabled = list(providers.get("enabled") or [])
    disabled = list(providers.get("disabled") or [])
    allowlist = providers.get("enabled_models")
    if allowlist is not None:
        allowlist = list(allowlist)

    if action in {"enable", "default"}:
        if provider not in enabled:
            enabled.append(provider)
        if allowlist is not None:
            for model in BUILTIN_PROVIDER_MODELS[provider]:
                if model not in allowlist:
                    allowlist.append(model)
        disabled = [name for name in disabled if name != provider]
    elif action == "disable":
        if providers.get("default", "deepseek") == provider:
            raise ValueError("The default provider cannot be disabled; select another default first")
        enabled = [name for name in enabled if name != provider]
        if provider not in disabled:
            disabled.append(provider)
        if allowlist is not None:
            allowlist = [model for model in allowlist if model not in BUILTIN_PROVIDER_MODELS[provider]]
    else:
        raise ValueError(f"Unsupported provider action: {action}")

    providers["enabled"] = enabled
    if action == "default":
        providers["default"] = provider
    elif "default" not in providers:
        providers["default"] = "deepseek"
    if allowlist is not None:
        providers["enabled_models"] = allowlist
    if disabled:
        providers["disabled"] = disabled
    else:
        providers.pop("disabled", None)
    updated["providers"] = providers
    return updated


def mark_provider_authenticated(config: dict[str, Any], provider: str) -> dict[str, Any]:
    """Enable a provider after login unless the user explicitly disabled it."""
    if provider not in BUILTIN_PROVIDER_MODELS:
        raise ValueError(f"Unsupported provider: {provider}")
    updated = dict(config)
    settings = dict(updated.get("providers", {}))
    if provider in set(settings.get("disabled") or []):
        return updated
    enabled = list(settings.get("enabled") or [])
    if provider not in enabled:
        enabled.append(provider)
    settings["enabled"] = enabled
    updated["providers"] = settings
    return updated


def validate_proxy(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError("Proxy must be a URL such as http://localhost:8080 or socks5://localhost:1080")
    return value


def update_provider_proxy(config: dict[str, Any], provider: str, proxy: str | None) -> dict[str, Any]:
    if provider not in BUILTIN_PROVIDER_MODELS:
        raise ValueError(f"Unsupported provider: {provider}")
    if proxy is not None:
        validate_proxy(proxy)
    updated = dict(config)
    provider_config = dict(updated.get(provider, {}))
    if proxy is None:
        provider_config.pop("proxy", None)
    else:
        provider_config["proxy"] = proxy
    updated[provider] = provider_config
    return updated


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=11555, ge=1, le=65535)
    api_key: str | None = None
    # Completed long-running requests remain pollable after a client disconnect.
    async_job_retention_ms: int = Field(default=3600000, ge=1000)


class BrowserConfig(BaseModel):
    # managed uses the installed Google Chrome with a Web Model Adapter profile; cdp
    # attaches to an already-running Chrome exposed through CDP.
    mode: Literal["managed", "cdp"] = "managed"
    headless: bool = True
    profile_dir: str = provider_profile_dir("deepseek")
    executable_path: str | None = None
    cdp_endpoint: str | None = None
    restart_retries: int = Field(default=1, ge=0, le=5)
    max_pages: int | None = Field(default=8, ge=1)
    idle_timeout_ms: int | None = Field(default=300000, ge=0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_cdp_config(cls, value: Any) -> Any:
        """Map pre-Stage-1 configs with only cdp_endpoint to CDP mode."""
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        if "mode" not in migrated and migrated.get("cdp_endpoint"):
            migrated["mode"] = "cdp"
        return migrated


class DeepSeekConfig(BaseModel):
    transport: str = "web"
    chat_url: str = "https://chat.deepseek.com/"
    timeout_ms: int = Field(default=180000, ge=1000)
    recovery_timeout_ms: int = Field(default=120000, ge=0)
    recovery_enabled: bool = True
    recovery_max_attempts: int = Field(default=2, ge=1, le=5)
    recovery_backoff_base_ms: int = Field(default=250, ge=0, le=60000)
    recovery_backoff_max_ms: int = Field(default=5000, ge=0, le=120000)
    login_timeout_ms: int = Field(default=30000, ge=1000)
    system_prompt: str = "Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required."
    proxy: str | None = None


class QwenConfig(BaseModel):
    chat_url: str = "https://chat.qwen.ai/auth"
    auth: str = "manual"
    profile_dir: str = provider_profile_dir("qwen")
    headless: bool = False
    timeout_ms: int = Field(default=180000, ge=1000)
    recovery_timeout_ms: int = Field(default=120000, ge=0)
    recovery_enabled: bool = True
    recovery_max_attempts: int = Field(default=2, ge=1, le=5)
    recovery_backoff_base_ms: int = Field(default=250, ge=0, le=60000)
    recovery_backoff_max_ms: int = Field(default=5000, ge=0, le=120000)
    models: list[str] = Field(default_factory=lambda: ["qwen-chat"])
    # Deliberately false until a repeatable live Qwen image probe succeeds.
    image_generation_verified: bool = False
    # Per-model opt-in for image generation. When omitted, the legacy global
    # flag above verifies only the original qwen-chat Create Image flow, so an
    # upgrade never silently advertises an unverified image model. Listing a
    # model here requires image_generation_verified=true as well.
    image_generation_verified_models: list[str] | None = None
    proxy: str | None = None


class ProviderConfig(BaseModel):
    default: str = "deepseek"
    enabled: list[str] = Field(default_factory=lambda: ["deepseek"])
    enabled_models: list[str] | None = None


class GatewayLimits(BaseModel):
    """Optional measurable gateway limits; upstream token limits remain unknown."""

    max_input_chars: int | None = Field(default=None, ge=1)
    max_output_chars: int | None = Field(default=None, ge=1)
    context_budget_chars: int | None = Field(default=24000, ge=1024)
    context_budget_profiles: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_context_profiles(self):
        if any(value < 1024 for value in self.context_budget_profiles.values()):
            raise ValueError("context budget profiles must be at least 1024 characters")
        return self


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str | None = "wmadapter.log"
    max_bytes: int = Field(default=1_000_000, ge=10_000)
    backup_count: int = Field(default=3, ge=0, le=20)


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    qwen: QwenConfig = Field(default_factory=QwenConfig)
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    limits: GatewayLimits = Field(default_factory=GatewayLimits)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def as_legacy_dict(self) -> dict[str, Any]:
        return self.model_dump()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    p = config_file_path(path)
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    try:
        return AppConfig.model_validate(data).as_legacy_dict()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration in {p}: {exc}") from exc
