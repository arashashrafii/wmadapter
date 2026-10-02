"""Provider-neutral request normalization for Qwen Image 3.

The public API exposes a normalized ``size``/``aspect_ratio`` pair. Qwen's
OpenAI-compatible image protocol consumes an explicit ``widthxheight`` value,
while Qwen Studio's Create Image UI exposes a fixed set of aspect-ratio
presets. This module owns the single documented mapping between them so that
the gateway never silently claims pixel dimensions the upstream flow does not
offer.

References:
- https://www.alibabacloud.com/help/en/model-studio/qwen-image-api
- https://www.alibabacloud.com/help/en/model-studio/qwen-image-generation-and-editing-api-reference
"""
from __future__ import annotations

import re
from types import MappingProxyType


# Provider model identifier registered by WM Adapter for the standard
# (non-Pro) Qwen Image 3 model exposed by Qwen Studio's Create Image workflow.
QWEN_IMAGE_MODEL = "qwen-image-3.0"

# Exact label Qwen Studio renders in the Create Image model dropdown. That
# dropdown defaults to "Qwen-Image 2.0", so the adapter must select Image 3
# explicitly to honour the requested model instead of silently generating with
# the older default.
QWEN_IMAGE_MODEL_UI_LABELS = MappingProxyType({
    "qwen-image-3.0": "Qwen-Image 3.0",
})

# Aspect-ratio presets selectable in Qwen Studio's Create Image dropdown, as
# observed in the live UI. "auto" is deliberately NOT a selectable preset: the
# live UI offers no Auto option, so an auto request leaves the provider's
# current default untouched instead of pretending to choose something that
# does not exist.
QWEN_IMAGE_ASPECT_RATIOS = (
    "auto", "1:1", "3:4", "4:3", "16:9", "9:16",
)
QWEN_IMAGE_STUDIO_PRESETS = ("1:1", "3:4", "4:3", "16:9", "9:16")

# Image rendering is far slower than a text turn: the Create Image UI still
# showed its "generating" indicator after the chat timeout elapsed. Give image
# generation its own budget, still bounded by the provider-wide request cap.
QWEN_IMAGE_TIMEOUT_MS = 600_000

# Deterministic preset -> Qwen OpenAI-compatible pixel-size mapping. Only these
# five shapes are selectable in the verified Web flow; arbitrary dimensions are
# not exposed through the Web adapter even when the standalone image API would
# accept them.
ASPECT_RATIO_SIZES = MappingProxyType({
    "1:1": "1024x1024",
    "3:4": "960x1280",
    "4:3": "1280x960",
    "16:9": "1280x720",
    "9:16": "720x1280",
})

# Qwen's documented OpenAI-compatible size grammar.
SIZE_PATTERN = re.compile(r"^(?P<width>[1-9]\d{0,4})x(?P<height>[1-9]\d{0,4})$", re.IGNORECASE)
# Relative tolerance used when matching a requested pixel shape to a verified
# preset. Aspect ratios are exact rationals, so a tight tolerance is enough to
# absorb client-side rounding such as 2048x1152 for 16:9.
ASPECT_RATIO_TOLERANCE = 0.01
# Sanity bound for requested dimensions. The Web flow selects one of five
# fixed preset shapes, so this only rejects nonsense input.
MAX_REQUESTED_DIMENSION = 16384


def qwen_image_preset_for_dimensions(width: int, height: int) -> str:
    """Return the verified preset matching a requested pixel shape.

    OpenAI-style clients request concrete pixel dimensions (for example
    ``2048x1152`` or ``3840x2160``) rather than Qwen Studio's presets. The Web
    flow can only produce the five preset *shapes*, so the requested shape is
    mapped to the preset with the same aspect ratio and the generated image
    uses that preset's canonical size. A shape whose ratio matches no preset has
    no deterministic Web representation and is rejected rather than
    approximated to an unrelated preset.
    """
    if width <= 0 or height <= 0 or width > MAX_REQUESTED_DIMENSION or height > MAX_REQUESTED_DIMENSION:
        raise ValueError("Image size dimensions must be positive and within 16384")
    ratio = width / height
    for aspect, preset in ASPECT_RATIO_SIZES.items():
        preset_width, preset_height = (int(value) for value in preset.split("x"))
        preset_ratio = preset_width / preset_height
        if abs(ratio - preset_ratio) <= ASPECT_RATIO_TOLERANCE * preset_ratio:
            return aspect
    supported = ", ".join(QWEN_IMAGE_STUDIO_PRESETS)
    raise ValueError(f"Image size aspect ratio must match one of: {supported}")


def normalize_qwen_image_size(*, size: str | None = None, aspect_ratio: str | None = None) -> str:
    """Return Qwen's exact ``widthxheight``/``auto`` value or raise safely.

    Raises ``ValueError`` for ambiguous or unsupported input. The message is
    safe to surface to clients: it contains no prompt, URL, or credential data.
    """
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
    # Map the requested pixel shape onto the verified preset with the same
    # aspect ratio; the Web flow has no pixel-dimension input, so the preset's
    # canonical size is what the upstream request actually selects.
    aspect = qwen_image_preset_for_dimensions(width, height)
    return ASPECT_RATIO_SIZES[aspect]


def qwen_image_aspect_for_size(size: str) -> str:
    """Return the Qwen Studio preset corresponding to a normalized size.

    A normalized pixel size that does not correspond to one of the five
    verified presets has no deterministic Web representation and is rejected
    rather than approximated.
    """
    if size == "auto":
        return "auto"
    for aspect, preset_size in ASPECT_RATIO_SIZES.items():
        if preset_size == size:
            return aspect
    raise ValueError("Image size has no supported Qwen Studio aspect-ratio preset")


def qwen_image_ui_label(model: str) -> str:
    """Return the Qwen Studio Create Image dropdown label for a model id."""
    label = QWEN_IMAGE_MODEL_UI_LABELS.get(model)
    if label is None:
        raise ValueError("Image model has no verified Qwen Studio label")
    return label
