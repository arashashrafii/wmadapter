from __future__ import annotations

import socket


def is_port_available(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
        except OSError:
            return False
    return True


def find_free_port(host: str, start_port: int, max_port: int = 65535) -> int:
    for port in range(start_port, max_port + 1):
        if is_port_available(host, port):
            return port
    raise RuntimeError(f"No free port found from {start_port} to {max_port}")
