from __future__ import annotations

import uvicorn

from .config import load_config


def main() -> None:
    config = load_config()
    server = config["server"]
    uvicorn.run(
        "webbridgefreeride.main:app",
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8000)),
        reload=False,
    )


if __name__ == "__main__":
    main()
