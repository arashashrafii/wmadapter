from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

from dotenv import load_dotenv
import uvicorn

from .config import (
    BUILTIN_PROVIDER_MODELS,
    config_file_path,
    load_config,
    provider_profile_from_config,
    save_config,
    update_provider_proxy,
    update_provider_config,
)
from .logging import configure_logging
from .manual_auth import run_manual_auth


def _pause_service_for_login() -> bool:
    """Stop the system service only when it owns the login profile."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    active = subprocess.run([systemctl, "is-active", "--quiet", "wmadapter.service"], check=False)
    if active.returncode != 0:
        return False
    command = [systemctl, "stop", "wmadapter.service"]
    if os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError("Login requires sudo to pause the running wmadapter.service")
        command.insert(0, sudo)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError("Could not stop wmadapter.service before login")
    return True


def _resume_service_after_login(paused: bool) -> None:
    if not paused:
        return
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise RuntimeError("systemctl is unavailable; start wmadapter.service manually")
    command = [systemctl, "start", "wmadapter.service"]
    if os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError("Login completed but sudo is unavailable to restart wmadapter.service")
        command.insert(0, sudo)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError("Login completed but wmadapter.service could not be restarted")


def _opencode_config_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else Path.home() / ".config" / "opencode" / "opencode.json"


def _run_opencode(provider: str, config: dict, output: str | None) -> Path:
    target = _opencode_config_path(output)
    if target.exists():
        try:
            raw = target.read_text(encoding="utf-8").strip()
            document = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenCode config is not valid JSON: {target}") from exc
    else:
        document = {}
    models = list(config.get(provider, {}).get("models") or BUILTIN_PROVIDER_MODELS[provider])
    provider_id = f"wmadapter-{provider}"
    providers = dict(document.get("provider") or {})
    providers[provider_id] = {
        "name": f"WM Adapter ({provider})",
        "npm": "@ai-sdk/openai-compatible",
        "options": {"baseURL": "http://127.0.0.1:11555/v1"},
        "models": {model: {"name": model} for model in models},
    }
    document["provider"] = providers
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _run_openclaw(provider: str, config: dict, output: str | None) -> Path:
    target = Path(output).expanduser() if output else Path.home() / ".openclaw" / "openclaw.json"
    if target.exists():
        try:
            raw = target.read_text(encoding="utf-8").strip()
            document = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenClaw config is not valid JSON: {target}") from exc
    else:
        document = {}
    models = list(config.get(provider, {}).get("models") or BUILTIN_PROVIDER_MODELS[provider])
    model_entries = [{"id": model, "name": model} for model in models]
    model_config = dict(document.get("models") or {})
    providers = dict(model_config.get("providers") or {})
    current = dict(providers.get("wmadapter") or {})
    current.update({
        "baseUrl": "http://127.0.0.1:11555/v1",
        "api": "openai-completions",
        "models": model_entries,
    })
    providers["wmadapter"] = current
    model_config["providers"] = providers
    document["models"] = model_config
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _check_ready(config: dict, provider: str | None = None) -> int:
    server = config.get("server", {})
    host = server.get("host", "127.0.0.1")
    port = int(server.get("port", 11555))
    url = f"http://{host}:{port}/ready"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"status": "not_ready", "error": str(exc)}
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print("not okay")
        return 1
    if provider:
        ready = payload.get("providers", {}).get(provider, {}).get("ready") is True
    else:
        ready = payload.get("status") == "ready"
    print("okay" if status == 200 and ready else "not okay")
    return 0 if status == 200 and ready else 1


def _service_checks(config: dict) -> tuple[bool, dict[str, str]]:
    server = config.get("server", {})
    host = server.get("host", "127.0.0.1")
    port = int(server.get("port", 11555))
    result = {"service": "unknown", "port": f"{host}:{port}", "health": "not okay", "ready": "not okay"}
    systemctl = shutil.which("systemctl")
    if systemctl:
        active = subprocess.run([systemctl, "is-active", "--quiet", "wmadapter.service"], check=False)
        result["service"] = "okay" if active.returncode == 0 else "not okay"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5) as response:
            result["health"] = "okay" if response.status == 200 else "not okay"
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            result["ready"] = "okay" if response.status == 200 and payload.get("status") == "ready" else "not okay"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        pass
    healthy = all(value == "okay" for key, value in result.items() if key != "port")
    return healthy, result


def _doctor(config: dict) -> int:
    healthy, result = _service_checks(config)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0 if healthy else 1


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="wmadapter", description="Web Model Adapter — Web-to-API Gateway for AI Agents")
    parser.add_argument(
        "--config",
        default=None,
        help="configuration file (also available as WMADAPTER_CONFIG)",
    )
    subparsers = parser.add_subparsers(dest="command")
    def add_auth_parser(name: str) -> None:
        auth = subparsers.add_parser(name)
        auth.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
        auth.add_argument("--google", action="store_true", help="Open provider login and start Google authentication when possible")
        auth.add_argument("--external-browser", action="store_true", help="Authenticate in system Chrome, then let the hidden service verify the profile")
        auth.add_argument("--executable-path", help="Google Chrome executable path")

    add_auth_parser("auth")
    add_auth_parser("login")

    provider = subparsers.add_parser("provider", help="Configure built-in web providers")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    provider_commands.add_parser("list", help="Show configured providers and profile locations")
    for action in ("enable", "disable", "default"):
        command = provider_commands.add_parser(action)
        command.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    proxy = subparsers.add_parser("proxy", help="Configure provider-specific proxies")
    proxy_commands = proxy.add_subparsers(dest="proxy_command", required=True)
    proxy_add = proxy_commands.add_parser("add", help="Set a proxy for one provider")
    proxy_add.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    proxy_add.add_argument("url")
    proxy_remove = proxy_commands.add_parser("remove", help="Remove a provider proxy")
    proxy_remove.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    proxy_commands.add_parser("list", help="Show provider proxies")
    add = subparsers.add_parser("add", help="Compatibility command group")
    add_commands = add.add_subparsers(dest="add_command", required=True)
    add_proxy = add_commands.add_parser("proxy", help="Set a proxy for one provider")
    add_proxy.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    add_proxy.add_argument("url")
    check = subparsers.add_parser("check", help="Check service state")
    check_commands = check.add_subparsers(dest="check_command")
    ready = check_commands.add_parser("ready", help="Check whether a provider is online and authenticated")
    ready.add_argument("provider", nargs="?", choices=sorted(BUILTIN_PROVIDER_MODELS))
    subparsers.add_parser("doctor", help="Diagnose the local WM Adapter service")
    run = subparsers.add_parser("run", help="Configure a client from WM Adapter models")
    run_commands = run.add_subparsers(dest="client", required=True)
    opencode = run_commands.add_parser("opencode", help="Add a WM Adapter provider to OpenCode")
    opencode.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    opencode.add_argument("--config", dest="client_config", help="OpenCode config file path")
    openclaw = run_commands.add_parser("openclaw", help="Add WM Adapter models to OpenClaw")
    openclaw.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    openclaw.add_argument("--config", dest="client_config", help="OpenClaw config file path")
    args = parser.parse_args()

    if args.command in {"auth", "login"}:
        config = load_config(args.config)
        paused = _pause_service_for_login()
        try:
            asyncio.run(
                run_manual_auth(
                    args.provider,
                    use_google=args.google,
                    external_browser=args.external_browser,
                    executable_path=args.executable_path,
                    config=config,
                )
            )
        finally:
            _resume_service_after_login(paused)
        return
    if args.command == "provider":
        config = load_config(args.config)
        path = config_file_path(args.config)
        if args.provider_command == "list":
            settings = config.get("providers", {})
            default = settings.get("default", "deepseek")
            enabled = set(settings.get("enabled") or [])
            print(f"config: {path}")
            print(f"default: {default}")
            for provider_name in sorted(BUILTIN_PROVIDER_MODELS):
                state = "enabled" if provider_name in enabled else "disabled"
                profile = provider_profile_from_config(config, provider_name)
                print(f"{provider_name}: {state}; profile={profile}")
            return
        try:
            updated = update_provider_config(config, args.provider_command, args.provider)
            save_config(updated, path)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Provider configuration updated: {args.provider_command} {args.provider}")
        return
    if args.command in {"proxy", "add"}:
        config = load_config(args.config)
        path = config_file_path(args.config)
        if args.command == "proxy" and args.proxy_command == "list":
            for provider_name in sorted(BUILTIN_PROVIDER_MODELS):
                value = config.get(provider_name, {}).get("proxy") or "none"
                print(f"{provider_name}: {value}")
            return
        provider_name = args.provider
        value = None if args.command == "proxy" and args.proxy_command == "remove" else args.url
        try:
            save_config(update_provider_proxy(config, provider_name, value), path)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Proxy {'removed for' if value is None else 'updated for'} {provider_name}")
        return
    if args.command == "run":
        config = load_config(args.config)
        try:
            path = (_run_opencode if args.client == "opencode" else _run_openclaw)(args.provider, config, args.client_config)
        except RuntimeError as exc:
            parser.error(str(exc))
        print(f"{args.client.title()} configured for {args.provider}: {path}")
        return
    if args.command == "check":
        config = load_config(args.config)
        if args.check_command == "ready":
            raise SystemExit(_check_ready(config, args.provider))
        healthy, _ = _service_checks(config)
        print("okay" if healthy else "not okay")
        raise SystemExit(0 if healthy else 1)
    if args.command == "doctor":
        raise SystemExit(_doctor(load_config(args.config)))
    if args.config:
        # Keep the existing environment-based entrypoint compatible while making
        # product/test selection explicit for local scripts and service units.
        import os
        os.environ["WMADAPTER_CONFIG"] = args.config
    run_server()


if __name__ == "__main__":
    main()
