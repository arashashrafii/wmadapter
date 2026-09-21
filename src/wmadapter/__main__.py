from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess

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
    if args.config:
        # Keep the existing environment-based entrypoint compatible while making
        # product/test selection explicit for local scripts and service units.
        import os
        os.environ["WMADAPTER_CONFIG"] = args.config
    run_server()


if __name__ == "__main__":
    main()
