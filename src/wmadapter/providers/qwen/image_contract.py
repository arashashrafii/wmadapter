"""Provider-neutral request normalization for Qwen Image 3."""
from __future__ import annotations

import re
from types import MappingProxyType


QWEN_IMAGE_MODEL = "qwen-image-3.0"
QWEN_IMAGE_ASPECT_RATIOS = (
    "auto", "1:1", "3:4", "4:3", "16:9", "9:16",
)
ASPECT_RATIO_SIZES = MappingProxyType({
    "1:1": "1024x1024",
    "3:4": "960x1280",
    "4:3": "1280x960",
    "16:9": "1280x720",
    "9:16": "720x1280",
})
SIZE_PATTERN = re.compile(r"^(?P<width>[1-9]\d{0,4})x(?P<height>[1-9]\d{0,4})$", re.IGNORECASE)
MIN_PIXEL_AREA = 512 * 512
MAX_PIXEL_AREA = 2048 * 2048
MIN_ASPECT_RATIO = 1 / 8
MAX_ASPECT_RATIO = 8


def normalize_qwen_image_size(*, size: str | None = None, aspect_ratio: str | None = None) -> str:
    """Return Qwen's exact ``widthxheight``/``auto`` value or raise safely."""
    if size is not None and aspect_ratio is not None:
        raise ValueError("Specify only one of size or aspect_ratio")
    value = aspect_ratio if aspect_ratio is not None else size
    if value is None:
        return "auto"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Image size or aspect_ratio must be a non-empty string")
    normalized = value.strip().lower()
    if normalized in {"auto", "automatic"}:
        return "auto"
    if normalized in ASPECT_RATIO_SIZES:
        return ASPECT_RATIO_SIZES[normalized]
    if aspect_ratio is not None:
        supported = ", ".join(QWEN_IMAGE_ASPECT_RATIOS)
        raise ValueError(f"Unsupported aspect_ratio; expected one of: {supported}")
    match = SIZE_PATTERN.fullmatch(normalized)
    if not match:
        raise ValueError("Unsupported image size; expected auto, an aspect preset, or widthxheight")
    width = int(match.group("width"))
    height = int(match.group("height"))
    area = width * height
    ratio = width / height
    if not MIN_PIXEL_AREA <= area <= MAX_PIXEL_AREA:
        raise ValueError("Image size pixel area must be between 512x512 and 2048x2048")
    if not MIN_ASPECT_RATIO <= ratio <= MAX_ASPECT_RATIO:
        raise ValueError("Image size aspect ratio must be between 1:8 and 8:1")
    return f"{width}x{height}"


def qwen_image_aspect_for_size(size: str) -> str:
    """Return the Qwen Studio preset corresponding to a normalized size."""
    if size == "auto":
        return "auto"
    for aspect, preset_size in ASPECT_RATIO_SIZES.items():
        if preset_size == size:
            return aspect
    raise ValueError("Image size has no supported Qwen Studio aspect-ratio preset")
