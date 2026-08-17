from __future__ import annotations

import argparse

from dotenv import load_dotenv
import uvicorn

from .config import load_config
from .credentials import save_credentials_interactive
from .logging import configure_logging
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
    args = parser.parse_args()

    if args.command == "credentials" and args.credential_command == "set":
        save_credentials_interactive()
        return
    run_server()


if __name__ == "__main__":
    main()
