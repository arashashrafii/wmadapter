from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import uvicorn
import yaml

from .config import load_config
from .logging import configure_logging
from .manual_auth import run_manual_auth
from .registry import SUPPORTED_PROVIDERS, discover_models


def run_server() -> None:
    load_dotenv()
    config = load_config()
    configure_logging(config["logging"])
    server = config["server"]
    host = server.get("host", "127.0.0.1")
    port = int(server.get("port", 11555))
    print(f"API URL: http://{host}:{port}/v1")
    uvicorn.run(
        "wmadapter.main:app",
        host=host,
        port=port,
        reload=False,
    )


def _mutable_config(path: str | None) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path or os.getenv("WMADAPTER_CONFIG", "config.yaml"))
    data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Configuration in {config_path} must be a YAML object")
    data.setdefault("providers", {})
    data["providers"].setdefault("default", "deepseek")
    data["providers"].setdefault("enabled", ["deepseek"])
    return config_path, data


def _save_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _provider_models(data: dict[str, Any], provider: str) -> list[str]:
    return [item.model for item in discover_models(data, provider)]


def _config_command(command: str, args) -> None:
    path, data = _mutable_config(args.config)
    provider_cfg = data["providers"]
    enabled = list(provider_cfg.get("enabled") or [])
    if command == "add-provider":
        if args.provider not in SUPPORTED_PROVIDERS:
            raise RuntimeError(f"Unsupported provider: {args.provider}")
        if args.provider not in enabled:
            enabled.append(args.provider)
        provider_cfg["enabled"] = enabled
        # An explicit allowlist is a user choice; extend it without replacing
        # any existing entries. An omitted allowlist remains unrestricted.
        if isinstance(provider_cfg.get("enabled_models"), list):
            current = list(provider_cfg["enabled_models"])
            for model in _provider_models(data, args.provider):
                if model not in current:
                    current.append(model)
            provider_cfg["enabled_models"] = current
        _save_config(path, data)
        print(json.dumps({"provider": args.provider, "enabled": True,
                          "models": _provider_models(data, args.provider)}, ensure_ascii=False))
        return
    if command == "remove-provider":
        if args.provider not in enabled:
            print(json.dumps({"provider": args.provider, "enabled": False}, ensure_ascii=False))
            return
        if len(enabled) == 1:
            raise RuntimeError("Cannot remove the last enabled provider")
        enabled.remove(args.provider)
        provider_cfg["enabled"] = enabled
        if provider_cfg.get("default") == args.provider:
            provider_cfg["default"] = enabled[0]
        _save_config(path, data)
        print(json.dumps({"provider": args.provider, "enabled": False}, ensure_ascii=False))
        return
    if command == "add-model":
        models = _provider_models(data, args.provider)
        if args.model not in models:
            raise RuntimeError(f"Unknown model {args.provider}:{args.model}")
        if args.provider not in enabled:
            raise RuntimeError(f"Provider {args.provider} is not enabled")
        allowlist = provider_cfg.get("enabled_models")
        if isinstance(allowlist, list) and args.model not in allowlist:
            allowlist.append(args.model)
            provider_cfg["enabled_models"] = allowlist
            _save_config(path, data)
        print(json.dumps({"provider": args.provider, "model": args.model, "enabled": True}, ensure_ascii=False))
        return
    if command == "remove-model":
        models = _provider_models(data, args.provider)
        if args.model not in models:
            raise RuntimeError(f"Unknown model {args.provider}:{args.model}")
        allowlist = provider_cfg.get("enabled_models")
        if allowlist is None:
            allowlist = [model for name in enabled for model in _provider_models(data, name)
                         if not (name == args.provider and model == args.model)]
        else:
            allowlist = [model for model in allowlist if model != args.model]
        provider_cfg["enabled_models"] = allowlist
        _save_config(path, data)
        print(json.dumps({"provider": args.provider, "model": args.model, "enabled": False}, ensure_ascii=False))
        return
    if command in {"list-providers", "list-models"}:
        config = load_config(path)
        configured = config["providers"].get("enabled") or []
        records = []
        for provider in configured:
            models = _provider_models(config, provider)
            records.append({"provider": provider, "enabled": True,
                            "default": provider == config["providers"].get("default"),
                            "models": models})
        if command == "list-models":
            allowed = config["providers"].get("enabled_models")
            records = [{"provider": record["provider"], "model": model,
                        "capability": next(item.capabilities for item in discover_models(config, record["provider"])
                                            if item.model == model),
                        "default": record["default"] and model == record["models"][0]}
                       for record in records for model in record["models"]
                       if allowed is None or model in allowed]
        print(json.dumps(records, ensure_ascii=False))
        return
    raise RuntimeError(f"Unsupported configuration command: {command}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wmadapter", description="Web Model Adapter — Web-to-API Gateway for AI Agents")
    parser.add_argument(
        "--config",
        default=None,
        help="configuration file (also available as WMADAPTER_CONFIG)",
    )
    subparsers = parser.add_subparsers(dest="command")
    def add_config_option(subparser):
        subparser.add_argument("--config", default=argparse.SUPPRESS,
                               help="configuration file (also available as WMADAPTER_CONFIG)")

    auth = subparsers.add_parser("auth")
    add_config_option(auth)
    auth.add_argument("provider", choices=["deepseek", "qwen"])
    auth.add_argument("--google", action="store_true", help="Open provider login and start Google authentication when possible")
    auth.add_argument("--external-browser", action="store_true", help="Authenticate in system Chrome, then let the hidden service verify the profile")
    auth.add_argument("--executable-path", help="Google Chrome executable path")
    add_provider = subparsers.add_parser("add-provider")
    add_config_option(add_provider)
    add_provider.add_argument("provider", choices=SUPPORTED_PROVIDERS)
    remove_provider = subparsers.add_parser("remove-provider")
    add_config_option(remove_provider)
    remove_provider.add_argument("provider", choices=SUPPORTED_PROVIDERS)
    add_model = subparsers.add_parser("add-model")
    add_config_option(add_model)
    add_model.add_argument("provider", choices=SUPPORTED_PROVIDERS)
    add_model.add_argument("model")
    remove_model = subparsers.add_parser("remove-model")
    add_config_option(remove_model)
    remove_model.add_argument("provider", choices=SUPPORTED_PROVIDERS)
    remove_model.add_argument("model")
    for name in ("list-providers", "list-models", "wizard"):
        command_parser = subparsers.add_parser(name)
        add_config_option(command_parser)
    args = parser.parse_args()

    if args.command == "auth":
        config = load_config(args.config)
        asyncio.run(
            run_manual_auth(
                args.provider,
                use_google=args.google,
                external_browser=args.external_browser,
                executable_path=args.executable_path,
                config=config,
            )
        )
        return
    if args.command in {"add-provider", "remove-provider", "add-model", "remove-model",
                        "list-providers", "list-models"}:
        try:
            _config_command(args.command, args)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        return
    if args.command == "wizard":
        print("Use add-provider/remove-provider and add-model/remove-model to configure providers.")
        return
    if args.config:
        # Keep the existing environment-based entrypoint compatible while making
        # product/test selection explicit for local scripts and service units.
        import os
        os.environ["WMADAPTER_CONFIG"] = args.config
    run_server()


if __name__ == "__main__":
    main()
