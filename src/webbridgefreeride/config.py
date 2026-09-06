from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


def env_value(canonical: str, legacy: str | None = None, default: str | None = None) -> str | None:
    value = os.getenv(canonical)
    if value is not None:
        return value
    return os.getenv(legacy, default) if legacy else default


def canonical_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=11555, ge=1, le=65535)
    api_key: str | None = None


class BrowserConfig(BaseModel):
    # managed uses MimicGate's existing persistent browser profile; cdp attaches
    # to an already-running Chromium exposed through CDP.
    mode: Literal["managed", "cdp"] = "managed"
    headless: bool = True
    profile_dir: str = ".webbridge-profile"
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
    login_timeout_ms: int = Field(default=30000, ge=1000)
    system_prompt: str = "Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required."


class QwenConfig(BaseModel):
    chat_url: str = "https://chat.qwen.ai/"
    auth: str = "manual"
    profile_dir: str = ".webbridge-profile/qwen"
    headless: bool = False
    timeout_ms: int = Field(default=180000, ge=1000)


class ProviderConfig(BaseModel):
    default: str = "deepseek"
    enabled: list[str] = Field(default_factory=lambda: ["deepseek"])


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str | None = "webbridgefreeride.log"
    max_bytes: int = Field(default=1_000_000, ge=10_000)
    backup_count: int = Field(default=3, ge=0, le=20)


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    qwen: QwenConfig = Field(default_factory=QwenConfig)
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def as_legacy_dict(self) -> dict[str, Any]:
        return self.model_dump()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    p = Path(path or env_value("MIMICGATE_CONFIG", "WEBBRIDGE_CONFIG", "config.yaml"))
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    try:
        return AppConfig.model_validate(data).as_legacy_dict()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration in {p}: {exc}") from exc
