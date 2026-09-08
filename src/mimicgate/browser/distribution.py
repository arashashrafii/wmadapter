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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from collections.abc import Mapping

import fcntl


SUPPORTED_PLATFORM = {
    "os": "linux",
    "arch": "x86_64",
    "distribution": "ubuntu",
    "release": "24.04",
}


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


@dataclass(frozen=True)
class BundleManifest:
    schema_version: int
    product: str
    playwright_version: str
    browser: str
    platform: dict[str, str]
    artifact: Artifact


@dataclass(frozen=True)
class PreflightResult:
    manifest: BundleManifest
    source: Path
    cache_path: Path
    destination: Path


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


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest field {field!r} must be a non-empty string")
    return value


def detect_platform() -> dict[str, str]:
    os_name = platform.system().lower()
    machine = platform.machine().lower().replace("amd64", "x86_64")
    distribution = "unknown"
    release = "unknown"
    try:
        release_data = platform.freedesktop_os_release()
        distribution = release_data.get("ID", distribution).lower()
        release = release_data.get("VERSION_ID", release).lower()
    except (AttributeError, OSError):
        pass
    return {"os": os_name, "arch": machine, "distribution": distribution, "release": release}


def validate_platform(manifest_platform: Mapping[str, object], host: Mapping[str, str] | None = None) -> None:
    if not isinstance(manifest_platform, Mapping):
        raise ValueError("manifest platform must be an object")
    expected = dict(host or detect_platform())
    actual = {field: manifest_platform.get(field) for field in SUPPORTED_PLATFORM}
    if actual != expected or expected != SUPPORTED_PLATFORM:
        raise ValueError("browser bundle platform is unsupported; expected Linux Ubuntu 24.04 x86-64")


def parse_manifest(manifest: Mapping[str, object], host: Mapping[str, str] | None = None) -> BundleManifest:
    if not isinstance(manifest, Mapping):
        raise ValueError("browser manifest must be an object")
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported browser manifest schema_version")
    product = _require_string(manifest.get("product"), "product")
    playwright_version = _require_string(manifest.get("playwright_version"), "playwright_version")
    browser = _require_string(manifest.get("browser"), "browser")
    if browser != "chromium":
        raise ValueError("only the Chromium local bundle is supported")
    manifest_platform = manifest.get("platform")
    validate_platform(manifest_platform, host)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or not isinstance(artifacts.get("chromium"), Mapping):
        raise ValueError("manifest must contain an artifacts.chromium object")
    raw_artifact = artifacts["chromium"]
    sha256 = _require_string(raw_artifact.get("sha256"), "artifacts.chromium.sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha256):
        raise ValueError("artifacts.chromium.sha256 must be a 64-character hexadecimal digest")
    size = raw_artifact.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("artifacts.chromium.size must be a positive integer")
    source = _require_string(raw_artifact.get("source", "local"), "artifacts.chromium.source")
    return BundleManifest(
        schema_version=1,
        product=product,
        playwright_version=playwright_version,
        browser=browser,
        platform={field: str(manifest_platform[field]) for field in SUPPORTED_PLATFORM},
        artifact=Artifact(source, sha256, size, raw_artifact.get("signature")),
    )


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


def cache_path(cache_dir: Path, key: DistributionKey, artifact: Artifact) -> Path:
    return cache_dir / key.value / f"chromium-{artifact.sha256}.bundle"


@contextmanager
def _exclusive_lock(path: Path):
    """Hold a durable POSIX lock across a cache or activation transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def cache_artifact(path: Path, cache_dir: Path, key: DistributionKey, artifact: Artifact) -> Path:
    target = cache_path(cache_dir, key, artifact)
    with _exclusive_lock(target.with_name(f".{target.name}.lock")):
        verify_artifact(path, artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(temporary_fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(path, temporary)
            verify_artifact(temporary, artifact)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def find_cached_artifact(cache_dir: Path, key: DistributionKey, artifact: Artifact) -> Path | None:
    candidate = cache_path(cache_dir, key, artifact)
    if not candidate.is_file():
        return None
    try:
        verify_artifact(candidate, artifact)
    except (OSError, ValueError):
        return None
    return candidate


def stage_artifact(path: Path, artifact: Artifact, staging: Path) -> Path:
    verify_artifact(path, artifact)
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / "chromium.bundle"
    shutil.copyfile(path, staged)
    verify_artifact(staged, artifact)
    return staged


def preflight_bundle(
    manifest: Mapping[str, object],
    source: Path,
    cache_dir: Path,
    destination: Path,
    host: Mapping[str, str] | None = None,
) -> PreflightResult:
    parsed = parse_manifest(manifest, host)
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"browser bundle does not exist: {source}")
    verify_artifact(source, parsed.artifact)
    cache_dir = Path(cache_dir)
    destination = Path(destination)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(cache_dir, os.W_OK):
        raise ValueError(f"browser cache is not writable: {cache_dir}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(destination.parent, os.W_OK):
        raise ValueError(f"browser destination is not writable: {destination.parent}")
    key = distribution_key(parsed.playwright_version, parsed.artifact.sha256)
    return PreflightResult(parsed, source, cache_path(cache_dir, key, parsed.artifact), destination)


def activate_atomically(staging: Path, destination: Path) -> None:
    if not staging.is_dir():
        raise ValueError("browser staging directory does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(destination.parent / f".{destination.name}.lock"):
        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
        backup = destination.parent / f".{destination.name}.previous-{os.getpid()}"
        try:
            shutil.copytree(staging, temporary / "payload", dirs_exist_ok=True)
            candidate = temporary / "payload"
            if destination.exists():
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(destination, backup)
            os.replace(candidate, destination)
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            if not destination.exists() and backup.exists():
                os.replace(backup, destination)
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
