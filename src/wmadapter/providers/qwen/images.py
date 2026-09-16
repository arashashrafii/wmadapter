"""Validation helpers for Qwen Web image artifacts.

The helpers deliberately accept only provider-owned HTTPS URLs and validate
the returned bytes before they can cross the gateway boundary.
"""
from __future__ import annotations

from urllib.parse import urlparse


MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_HOST_SUFFIXES = ("cdn.qwenlm.ai", "img.alicdn.com")
MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


def validate_artifact_url(url: str) -> str:
    """Return a safe artifact URL or raise without exposing its value."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host:
        raise ValueError("Qwen image artifact must use HTTPS")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        raise ValueError("Qwen image artifact host is not allowed")
    return url


def validate_image_bytes(content: bytes, content_type: str | None = None) -> tuple[bytes, str]:
    """Validate bounded image bytes and return canonical content plus MIME."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Qwen image artifact has an invalid size")
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime not in MAGIC:
        raise ValueError("Qwen image artifact has an unsupported MIME type")
    if not any(content.startswith(prefix) for prefix in MAGIC[mime]):
        raise ValueError("Qwen image artifact bytes do not match its MIME type")
    if mime == "image/webp" and content[8:12] != b"WEBP":
        raise ValueError("Qwen image artifact bytes do not match its MIME type")
    return content, mime
