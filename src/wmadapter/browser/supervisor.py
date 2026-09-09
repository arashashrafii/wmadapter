"""Cross-platform ownership primitives for managed browser processes."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


class ManagedProcess:
    """Own a launched process and reap its process group without broad kills."""

    def __init__(self, process: subprocess.Popen, profile: str):
        self.process = process
        self.profile = str(Path(profile).expanduser().resolve())
        self.pid = process.pid
        self.process_group = os.getpgid(self.pid) if hasattr(os, "getpgid") else self.pid

    def terminate(self, timeout: float = 5.0) -> None:
        if self.process.poll() is not None:
            return
        if os.name == "nt":
            self.process.terminate()
        else:
            os.killpg(self.process_group, signal.SIGTERM)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                self.process.kill()
            else:
                os.killpg(self.process_group, signal.SIGKILL)
            self.process.wait(timeout=timeout)


def launch_owned(command: list[str], profile: str, **kwargs) -> ManagedProcess:
    """Launch a command in its own group; callers must retain the owner."""
    if not command:
        raise ValueError("managed command cannot be empty")
    popen_kwargs = dict(kwargs)
    if os.name != "nt":
        popen_kwargs.setdefault("start_new_session", True)
    else:
        popen_kwargs.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)
    return ManagedProcess(subprocess.Popen(command, **popen_kwargs), profile)
