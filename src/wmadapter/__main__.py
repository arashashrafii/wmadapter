from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv
import uvicorn

from .config import load_config
from .logging import configure_logging
from .manual_auth import run_manual_auth


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
    subparsers = parser.add_subparsers(dest="command")
    auth = subparsers.add_parser("auth")
    auth.add_argument("provider", choices=["deepseek", "qwen"])
    auth.add_argument("--google", action="store_true", help="Open provider login and start Google authentication when possible")
    auth.add_argument("--external-browser", action="store_true", help="Authenticate in system Chrome, then let the hidden service verify the profile")
    auth.add_argument("--executable-path", help="Google Chrome executable path")
    args = parser.parse_args()

    if args.command == "auth":
        config = load_config()
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
    run_server()


if __name__ == "__main__":
    main()
