# Qwen Image 3 contract

WM Adapter exposes Qwen Image 3 as the provider model `qwen-image-3.0` on the
existing `POST /v1/images` endpoint. This is a provider-layer contract; no
OpenClaw or OpenCode behavior is implemented here.

## Configuration and discovery

Register the model under `qwen.models`. It is listed by `/v1/models` even while
unverified, but `capabilities.image_generation` remains false until the model
is explicitly opted into `qwen.image_generation_verified_models` and the
legacy `qwen.image_generation_verified` flag is true. Existing configurations
that only set the legacy flag continue to verify `qwen-chat` only.

```yaml
qwen:
  models:
    - qwen-chat
    - qwen-image-3.0
  image_generation_verified: true
  image_generation_verified_models:
    - qwen-chat
    - qwen-image-3.0
```

## Request shape

```json
{
  "model": "qwen-image-3.0",
  "prompt": "a paper lantern beside a rainy window",
  "aspect_ratio": "16:9",
  "n": 1,
  "response_format": "b64_json"
}
```

The gateway accepts exactly one of `size` and `aspect_ratio`:

- `aspect_ratio`: `auto`, `1:1`, `3:4`, `4:3`, `16:9`, or `9:16`.
- `size`: `auto`, one of those presets, or explicit `widthxheight`.

Presets map deterministically to Qwen Image 3 OpenAI-compatible sizes:
`1:1 → 1024x1024`, `3:4 → 960x1280`, `4:3 → 1280x960`,
`16:9 → 1280x720`, and `9:16 → 720x1280`. Explicit dimensions are validated
against Qwen's documented 512×512 through 2048×2048 pixel area and 1:8 through
8:1 aspect-ratio limits, then must correspond to one of the five supported Web
presets. This prevents the Web adapter from silently claiming pixel-perfect
control that the current Qwen Studio flow does not expose. Qwen's
OpenAI-compatible protocol uses `x`; the DashScope `*` separator is rejected.

`qwen-chat` remains backward-compatible: requests without size parameters use
the existing Create Image flow. Size and aspect-ratio parameters are reserved
for `qwen-image-3.0` so a client cannot accidentally imply that the chat model
supports the Image 3 contract.

## Response and failure behavior

Successful responses retain the existing base64 artifact shape. Returned image
bytes are bounded and checked against PNG, JPEG, GIF, or WebP magic bytes before
serialization. Unknown models, unsupported parameters, ambiguous size inputs,
unverified models, and provider failures produce deterministic redacted errors;
provider URLs, prompts, credentials, and response bodies are not exposed.

The current adapter reaches Qwen through the authenticated Qwen Web/Create
Image session. The official Qwen Image API documents the equivalent Image 3
request as a top-level `model`, `prompt`, and `size` request using the
OpenAI-compatible `widthxheight` format. Web UI availability, model selectors,
account entitlement, and exact rendered dimensions remain provider-side
runtime facts and require a live probe before enabling the model.
