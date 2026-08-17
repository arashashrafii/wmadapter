from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv
import uvicorn

from .config import load_config
from .credentials import save_credentials_interactive
from .logging import configure_logging
from .manual_auth import run_manual_auth
from .ports import find_free_port


def run_server() -> None:
    load_dotenv()
    config = load_config()
    configure_logging(config["logging"])
    server = config["server"]
    host = server.get("host", "127.0.0.1")
    configured_port = int(server.get("port", 11555))
    port = find_free_port(host, configured_port)
    if port != configured_port:
        print(f"Port {configured_port} is busy; using {port} instead.")
    print(f"API URL: http://{host}:{port}/v1")
    uvicorn.run(
        "webbridgefreeride.main:app",
        host=host,
        port=port,
        reload=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="webbridgefreeride")
    subparsers = parser.add_subparsers(dest="command")
    credentials = subparsers.add_parser("credentials")
    credential_commands = credentials.add_subparsers(dest="credential_command")
    credential_commands.add_parser("set")
    auth = subparsers.add_parser("auth")
    auth.add_argument("provider", choices=["qwen"])
    auth.add_argument("--google", action="store_true", help="Open provider login and start Google authentication when possible")
    auth.add_argument("--executable-path", help="Chrome/Chromium executable path")
    args = parser.parse_args()

    if args.command == "credentials" and args.credential_command == "set":
        save_credentials_interactive()
        return
    if args.command == "auth":
        asyncio.run(
            run_manual_auth(
                args.provider,
                use_google=args.google,
                executable_path=args.executable_path,
            )
        )
        return
    run_server()


if __name__ == "__main__":
    main()
