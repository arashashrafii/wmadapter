"""Verified Playwright browser distribution primitives.

Network transport and signature verification are injected so installers can use
an offline bundle, cache, CDN, or signed mirror without trusting browser PATH.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class DistributionKey:
    os_name: str
    arch: str
    playwright: str
    browser: str

    @property
    def value(self) -> str:
        return ":".join((self.os_name, self.arch, self.playwright, self.browser))


@dataclass(frozen=True)
class Artifact:
    source: str
    sha256: str
    size: int
    signature: str | None = None


def distribution_key(playwright_version: str, browser_hash: str) -> DistributionKey:
    return DistributionKey(platform.system().lower(), platform.machine().lower(), playwright_version, browser_hash)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, artifact: Artifact) -> None:
    if path.stat().st_size != artifact.size:
        raise ValueError(f"browser artifact size mismatch: {path}")
    if sha256_file(path).lower() != artifact.sha256.lower():
        raise ValueError(f"browser artifact SHA256 mismatch: {path}")


def verify_manifest(manifest: dict, signature_verifier: Callable[[bytes, str], bool] | None = None) -> None:
    payload = json.dumps(manifest.get("artifacts", {}), sort_keys=True, separators=(",", ":")).encode()
    signature = manifest.get("signature")
    if signature_verifier is None:
        if signature:
            raise ValueError("signed browser manifest requires an injected verifier")
        return
    if not signature or not signature_verifier(payload, signature):
        raise ValueError("browser manifest signature verification failed")


def source_order(offline: Iterable[Path], cache: Iterable[Path], cdn: Iterable[str], mirrors: Iterable[str]) -> list[str | Path]:
    return [*offline, *cache, *cdn, *mirrors]


def activate_atomically(staging: Path, destination: Path) -> None:
    if not staging.is_dir():
        raise ValueError("browser staging directory does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        shutil.copytree(staging, temporary / "payload", dirs_exist_ok=True)
        candidate = temporary / "payload"
        os.replace(candidate, destination)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
