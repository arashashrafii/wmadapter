from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv
import uvicorn
import yaml

from .config import (
    BUILTIN_PROVIDER_MODELS,
    config_file_path,
    load_config,
    provider_profile_from_config,
    save_config,
    update_provider_proxy,
    update_provider_config,
    mark_provider_authenticated,
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
    server = config.get("server") or {}
    host = server.get("host", "127.0.0.1")
    port = int(server.get("port", 11555))
    model_config = dict(document.get("models") or {})
    providers = dict(model_config.get("providers") or {})
    current = dict(providers.get("wmadapter") or {})
    existing_models = {
        item.get("id"): item
        for item in (current.get("models") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for model in models:
        current_model = dict(existing_models.get(model) or {})
        current_model.update({
            "id": model,
            "name": model,
            # WMAdapter normalizes browser text into OpenAI-compatible
            # function calls; tell OpenClaw not to suppress the tool surface.
            "compat": {**dict(current_model.get("compat") or {}), "supportsTools": True},
        })
        existing_models[model] = current_model
    model_entries = list(existing_models.values())
    current.update({
        "baseUrl": f"http://{host}:{port}/v1",
        "api": "openai-completions",
        # Browser-backed tool chains can require several provider turns before
        # the final answer. Keep the client connection alive long enough for
        # the gateway's heartbeat/polling behavior to do its job.
        "timeoutSeconds": 900,
        "models": model_entries,
    })
    providers["wmadapter"] = current
    model_config["providers"] = providers
    document["models"] = model_config
    tools_config = dict(document.get("tools") or {})
    # A custom OpenAI-compatible provider is not classified as a local Ollama
    # or LM Studio route by OpenClaw, so its automatic Tool Search default does
    # not apply. Directory mode keeps the capability catalog discoverable
    # while deferring optional schemas, preventing long sessions from
    # overflowing before Browser can be searched.
    tools_config["toolSearch"] = {"mode": "directory"}
    document["tools"] = tools_config
    agents = dict(document.get("agents") or {})
    defaults = dict(agents.get("defaults") or {})
    experimental = dict(defaults.get("experimental") or {})
    # This provider is not a local-model route.  Lean mode removes optional
    # tools such as Browser, so it must be disabled for OpenClaw sessions that
    # need the complete catalog.  Structured Tool Search still keeps the
    # provider prompt compact by deferring full schemas until requested.
    experimental["localModelLean"] = False
    defaults["experimental"] = experimental
    agents["defaults"] = defaults
    document["agents"] = agents
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _client_models(provider: str, config: dict) -> list[str]:
    return list(config.get(provider, {}).get("models") or BUILTIN_PROVIDER_MODELS[provider])


def _gateway_url(config: dict) -> str:
    server = config.get("server") or {}
    return f"http://{server.get('host', '127.0.0.1')}:{int(server.get('port', 11555))}/v1"


def _run_pi(provider: str, config: dict, output: str | None) -> Path:
    target = Path(output).expanduser() if output else Path.home() / ".pi" / "agent" / "models.json"
    if target.exists():
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Pi config is not valid JSON: {target}") from exc
    else:
        document = {}
    models = _client_models(provider, config)
    providers = dict(document.get("providers") or {})
    current = dict(providers.get(f"wmadapter-{provider}") or {})
    current.update({
        "baseUrl": _gateway_url(config),
        "api": "openai-completions",
        "apiKey": "not-needed",
        "models": [{"id": model, "name": model} for model in models],
    })
    providers[f"wmadapter-{provider}"] = current
    document["providers"] = providers
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _run_dsh(provider: str, config: dict, output: str | None) -> Path:
    dsh_home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))).expanduser()
    target = Path(output).expanduser() if output else dsh_home / "profiles" / "web" / "cordis.patch.yml"
    if target.exists():
        try:
            document = yaml.safe_load(target.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            raise RuntimeError(f"DeepSeek Harness config is not valid YAML: {target}") from exc
    else:
        document = []
    if not isinstance(document, list):
        raise RuntimeError(f"DeepSeek Harness config must contain a YAML patch list: {target}")
    models = _client_models(provider, config)
    providers = {f"wmadapter-{provider}": {
        "api": "openai-completions",
        "baseURL": _gateway_url(config),
        "models": [{"id": model} for model in models],
    }}
    patch = next((item for item in document if isinstance(item, dict) and item.get("id") == "llm-pi-ai"), None)
    if patch is None:
        document.append({"id": "llm-pi-ai", "config": {"providers": providers}})
    else:
        patch.setdefault("config", {})["providers"] = {**dict(patch.get("config", {}).get("providers") or {}), **providers}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
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


def _service_checks(config: dict) -> tuple[bool, dict[str, object]]:
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
    ready_payload: dict = {}
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=5) as response:
            ready_payload = json.loads(response.read().decode("utf-8"))
            result["ready"] = "okay" if response.status == 200 and ready_payload.get("status") == "ready" else "not okay"
    except urllib.error.HTTPError as exc:
        try:
            ready_payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    providers = {}
    for name, status in (ready_payload.get("providers") or {}).items():
        providers[name] = {
            "login": "okay" if status.get("ready") else "not okay",
            "browser": status.get("browser_running", False),
            "state": status.get("state", "unknown"),
            "reason": status.get("reason_code") or "none",
            "error": status.get("last_error") or "none",
        }
    result["providers"] = providers
    healthy = all(value == "okay" for key, value in result.items() if key not in {"port", "providers"})
    healthy = healthy and all(item["login"] == "okay" for item in providers.values())
    return healthy, result


def _doctor(config: dict) -> int:
    healthy, result = _service_checks(config)
    for key, value in result.items():
        if key == "providers":
            for provider, status in value.items():
                print(f"provider.{provider}.login: {status['login']}")
                print(f"provider.{provider}.browser: {status['browser']}")
                print(f"provider.{provider}.state: {status['state']}")
                print(f"provider.{provider}.reason: {status['reason']}")
                print(f"provider.{provider}.error: {status['error']}")
        else:
            print(f"{key}: {value}")
    return 0 if healthy else 1


def _fix_service(config: dict) -> None:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise RuntimeError("systemctl is unavailable")
    command = [systemctl, "restart", "wmadapter.service"]
    if os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError("sudo is unavailable")
        command.insert(0, sudo)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError("could not restart wmadapter.service")
    server = config.get("server", {})
    health_url = f"http://{server.get('host', '127.0.0.1')}:{int(server.get('port', 11555))}/health"
    ready_url = f"http://{server.get('host', '127.0.0.1')}:{int(server.get('port', 11555))}/ready"
    health_seen = False
    for _ in range(60):
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    health_seen = True
                    try:
                        with urllib.request.urlopen(ready_url, timeout=1) as ready_response:
                            payload = json.loads(ready_response.read().decode("utf-8"))
                    except urllib.error.HTTPError as exc:
                        payload = json.loads(exc.read().decode("utf-8"))
                    providers = payload.get("providers") or {}
                    transient = {"STARTING", "CHECKING_SESSION", "AUTHENTICATING", "HANDOFF", "VERIFYING_SESSION"}
                    if payload.get("status") == "ready" or not any(item.get("state") in transient for item in providers.values()):
                        return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    if health_seen:
        return
    raise RuntimeError("service did not become healthy after restart")


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
    parser = argparse.ArgumentParser(
        prog="wmadapter",
        description="Web Model Adapter — Web-to-API Gateway for AI Agents",
        epilog=(
            "Examples:\n"
            "  wmadapter login (deepseek/qwen)\n"
            "  wmadapter proxy add (deepseek/qwen) http://localhost:8080\n"
            "  wmadapter check ready qwen\n"
            "  wmadapter service restart\n"
            "  wmadapter doctor --fix\n"
            "  wmadapter run (opencode/openclaw/dsh/pi) (deepseek/qwen)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=None,
        help="configuration file (also available as WMADAPTER_CONFIG)",
    )
    subparsers = parser.add_subparsers(dest="command")
    def add_auth_parser(name: str) -> None:
        auth = subparsers.add_parser(name, help="Authenticate a provider in its browser profile")
        auth.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
        auth.add_argument("--external-browser", action="store_true", help="Authenticate in system Chrome, then let the hidden service verify the profile")
        auth.add_argument("--executable-path", help="Google Chrome executable path")

    add_auth_parser("login")

    provider = subparsers.add_parser("provider", help="Configure built-in web providers")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True, title="provider commands")
    provider_commands.add_parser("list", help="Show configured providers and profile locations")
    for action in ("enable", "disable", "default"):
        command = provider_commands.add_parser(action)
        command.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    proxy = subparsers.add_parser("proxy", help="Configure provider-specific proxies")
    proxy_commands = proxy.add_subparsers(dest="proxy_command", required=True, title="proxy commands")
    proxy_add = proxy_commands.add_parser("add", help="Set a proxy for one provider")
    proxy_add.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    proxy_add.add_argument("url")
    proxy_remove = proxy_commands.add_parser("remove", help="Remove a provider proxy")
    proxy_remove.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    proxy_commands.add_parser("list", help="Show provider proxies")
    check = subparsers.add_parser("check", help="Check service state")
    check_commands = check.add_subparsers(dest="check_command", title="check commands")
    ready = check_commands.add_parser("ready", help="Check whether a provider is online and authenticated")
    ready.add_argument("provider", nargs="?", choices=sorted(BUILTIN_PROVIDER_MODELS))
    service = subparsers.add_parser("service", help="Control the local WM Adapter service")
    service_commands = service.add_subparsers(dest="service_command", required=True, title="service commands")
    service_commands.add_parser("restart", help="Restart wmadapter.service and wait until it is healthy")
    doctor = subparsers.add_parser("doctor", help="Diagnose the local WM Adapter service")
    doctor.add_argument("--fix", action="store_true", help="Restart the service, then run diagnostics")
    run = subparsers.add_parser("run", help="Configure a client from WM Adapter models")
    run_commands = run.add_subparsers(dest="client", required=True)
    opencode = run_commands.add_parser("opencode", help="Add a WM Adapter provider to OpenCode")
    opencode.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    opencode.add_argument("--config", dest="client_config", help="OpenCode config file path")
    openclaw = run_commands.add_parser("openclaw", help="Add WM Adapter models to OpenClaw")
    openclaw.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    openclaw.add_argument("--config", dest="client_config", help="OpenClaw config file path")
    dsh = run_commands.add_parser("dsh", aliases=["deepseek-harness"], help="Add WM Adapter models to DeepSeek Harness")
    dsh.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    dsh.add_argument("--config", dest="client_config", help="DeepSeek Harness cordis.patch.yml path")
    pi = run_commands.add_parser("pi", help="Add WM Adapter models to Pi")
    pi.add_argument("provider", choices=sorted(BUILTIN_PROVIDER_MODELS))
    pi.add_argument("--config", dest="client_config", help="Pi models.json path")
    args = parser.parse_args()

    if args.command == "login":
        config = load_config(args.config)
        paused = _pause_service_for_login()
        try:
            asyncio.run(
                run_manual_auth(
                    args.provider,
                    external_browser=args.external_browser,
                    executable_path=args.executable_path,
                    config=config,
                )
            )
            save_config(mark_provider_authenticated(config, args.provider), config_file_path(args.config))
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
            disabled = set(settings.get("disabled") or [])
            print(f"config: {path}")
            print(f"default: {default}")
            for provider_name in sorted(BUILTIN_PROVIDER_MODELS):
                state = "disabled" if provider_name in disabled else ("enabled" if provider_name in enabled else "not logged in")
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
    if args.command == "proxy":
        config = load_config(args.config)
        path = config_file_path(args.config)
        if args.command == "proxy" and args.proxy_command == "list":
            for provider_name in sorted(BUILTIN_PROVIDER_MODELS):
                value = config.get(provider_name, {}).get("proxy") or "none"
                print(f"{provider_name}: {value}")
            return
        provider_name = args.provider
        value = None if args.proxy_command == "remove" else args.url
        try:
            save_config(update_provider_proxy(config, provider_name, value), path)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Proxy {'removed for' if value is None else 'updated for'} {provider_name}")
        return
    if args.command == "run":
        config = load_config(args.config)
        try:
            writers = {"opencode": _run_opencode, "openclaw": _run_openclaw, "dsh": _run_dsh,
                       "deepseek-harness": _run_dsh, "pi": _run_pi}
            path = writers[args.client](args.provider, config, args.client_config)
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
    if args.command == "service":
        config = load_config(args.config)
        if args.service_command == "restart":
            try:
                _fix_service(config)
            except RuntimeError as exc:
                print(f"restart: not okay ({exc})")
                raise SystemExit(1)
            print("wmadapter.service restarted and healthy")
            return
    if args.command == "doctor":
        config = load_config(args.config)
        if args.fix:
            try:
                _fix_service(config)
            except RuntimeError as exc:
                print(f"fix: not okay ({exc})")
                raise SystemExit(1)
        raise SystemExit(_doctor(config))
    if args.config:
        # Keep the existing environment-based entrypoint compatible while making
        # product/test selection explicit for local scripts and service units.
        import os
        os.environ["WMADAPTER_CONFIG"] = args.config
    run_server()


if __name__ == "__main__":
    main()
