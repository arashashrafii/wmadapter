from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


def canonical_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def provider_profile_dir(provider: str) -> str:
    root = Path(os.getenv("WMADAPTER_DATA_DIR", "~/.local/share/wmadapter")).expanduser()
    return str(root / "profiles" / provider)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=11555, ge=1, le=65535)
    api_key: str | None = None


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
    recovery_deadline_ms: int = Field(default=120000, ge=0, le=900000)
    # Ambiguous browser submissions are never replayed by default.
    recovery_allow_resend: bool = False
    login_timeout_ms: int = Field(default=30000, ge=1000)
    system_prompt: str = "Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required."


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
    recovery_deadline_ms: int = Field(default=120000, ge=0, le=900000)
    recovery_allow_resend: bool = False
    models: list[str] = Field(default_factory=lambda: ["qwen-chat"])
    # Deliberately false until a repeatable live Qwen image probe succeeds.
    image_generation_verified: bool = False


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
    p = Path(path or os.getenv("WMADAPTER_CONFIG", "config.yaml"))
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    try:
        return AppConfig.model_validate(data).as_legacy_dict()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration in {p}: {exc}") from exc
