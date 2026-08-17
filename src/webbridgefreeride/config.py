from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class BrowserConfig(BaseModel):
    headless: bool = True
    profile_dir: str = ".webbridge-profile"
    executable_path: str | None = None
    restart_retries: int = Field(default=1, ge=0, le=5)


class DeepSeekConfig(BaseModel):
    chat_url: str = "https://chat.deepseek.com/"
    timeout_ms: int = Field(default=180000, ge=1000)
    login_timeout_ms: int = Field(default=30000, ge=1000)
    system_prompt: str = "Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required."


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
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def as_legacy_dict(self) -> dict[str, Any]:
        return self.model_dump()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    p = Path(path or os.getenv("WEBBRIDGE_CONFIG", "config.yaml"))
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    try:
        return AppConfig.model_validate(data).as_legacy_dict()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration in {p}: {exc}") from exc
