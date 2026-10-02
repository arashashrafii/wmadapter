# Qwen Image 3 contract

WM Adapter exposes Qwen Image 3 as the provider model `qwen-image-3.0` on the
existing `POST /v1/images` endpoint. This is a provider-layer contract; the
gateway stays provider-neutral and adds no client-specific behavior.

## Configuration and discovery

Register the model under `qwen.models`:

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

`qwen-image-3.0` is listed by `/v1/models` once its provider is ready, but its
`capabilities.image_generation` stays `false` until the model is also listed in
`qwen.image_generation_verified_models` **and** the legacy
`qwen.image_generation_verified` flag is `true`.

This opt-in is deliberate:

- Omitting `image_generation_verified_models` keeps the previous behavior: the
  legacy flag alone verifies only the original `qwen-chat` Create Image flow.
- An upgrade therefore never silently advertises an image model whose live
  model/UI flow has not been verified.

`GET /props?model=qwen-image-3.0` reports the same per-model capability view,
so a client can discover whether the *selected* model supports image
generation before submitting.

## Request shape

The endpoint is served at both paths:

| Path | Used by |
| ---- | ------- |
| `POST /v1/images` | WM Adapter's own documented path |
| `POST /v1/images/generations` | OpenAI-SDK clients (OpenClaw's `image_generate` tool, Pi, compatible agents) |

Serving the canonical OpenAI path is what makes a native OpenAI-compatible
client work with no client-side plugin. No OpenClaw/OpenCode code is required
in this repository: a client is pointed at the gateway with
`baseUrl: http://127.0.0.1:11555/v1` and selects the model.

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

- `aspect_ratio`: `auto`, `1:1`, `3:4`, `4:3`, `16:9`, or `9:16` — the values
  verified in Qwen Studio's Create Image UI. The camelCase `aspectRatio`
  spelling is accepted as an alias, because OpenAI-SDK clients use that name.
- `size`: `auto`, one of those presets, or explicit `widthxheight` pixels.

### Preset mapping

Presets map deterministically to Qwen's OpenAI-compatible pixel sizes:

| Preset | Qwen size  |
| ------ | ---------- |
| `1:1`  | `1024x1024`|
| `3:4`  | `960x1280` |
| `4:3`  | `1280x960` |
| `16:9` | `1280x720` |
| `9:16` | `720x1280` |

`auto` is different in kind: the live Qwen Studio Create Image dropdown has no
Auto option, so an auto request leaves the provider's currently selected
default untouched instead of pretending to choose a preset that does not
exist. The UI-selectable presets are therefore exactly the five numeric ones
above.

### Verified Qwen Studio UI mapping

The adapter drives the authenticated Create Image workflow. The mapping below
was verified against the live Qwen Studio interface:

| Contract value | Qwen Studio element |
| -------------- | ------------------- |
| mode | `.mode-select-open` (`aria-label="Select Mode"`) → `Create Image` menu item |
| image model | composer footer model dropdown, label `Qwen-Image N.0` |
| aspect ratio | composer footer ratio dropdown, label `W:H` |

Two provider behaviours are handled explicitly:

- **The model dropdown defaults to `Qwen-Image 2.0`.** Qwen Studio's Create
  Image dropdown lists both `Qwen-Image 3.0` and `Qwen-Image 2.0`, and the
  older model is selected by default. A `qwen-image-3.0` request therefore
  selects `Qwen-Image 3.0` explicitly; without that step the adapter would
  silently generate with Image 2.0 while claiming to serve Image 3.
- **Template thumbnails share the artifact CDN.** The Create Image landing
  page preloads example thumbnails from the same provider host as real
  artifacts, so the adapter snapshots the rendered image sources *before*
  submitting and only accepts a source that appears afterwards.

### Pixel dimensions versus presets

OpenAI-compatible clients request concrete pixel dimensions, not presets.
OpenClaw, for example, translates `--aspect-ratio 16:9` into
`size: "2048x1152"` before sending the request. The Web flow, however, has no
pixel-dimension input: it can only select one of the five preset shapes.

The mapping is therefore explicit and documented:

1. The `widthxheight` value must use the OpenAI-compatible `x` separator with
   positive integers. The DashScope `*` separator and non-numeric values are
   rejected.
2. The requested shape is matched to the preset with the **same aspect ratio**,
   within a 1% relative tolerance. This absorbs client-side rounding such as
   `2048x1152` for 16:9.
3. The generated image uses that preset's canonical size. Since the upstream
   flow offers no resolution control, a request only selects the *shape* —
   `2048x1152`, `1600x900`, and `3840x2160` all produce the 16:9 preset at
   `1280x720`.
4. A shape whose ratio matches no preset (for example `1024x1536`, which is
   2:3) has no deterministic Web representation and is rejected. It is never
   approximated to an unrelated preset.

This keeps the adapter usable from standard OpenAI clients while never
claiming pixel-perfect control the current Qwen Studio flow does not expose.
Clients that need an exact pixel geometry must select a provider that exposes
it; `image_generation` capability metadata does not assert resolution control.

## Backward compatibility

`qwen-chat` keeps its existing behavior:

- Requests without `size`/`aspect_ratio` use the original Create Image flow.
- `size` and `aspect_ratio` are rejected for every model other than
  `qwen-image-3.0`, so a client cannot accidentally imply the chat model
  supports the Image 3 contract.

## Response contract

Successful responses keep the existing base64 artifact shape:

```json
{
  "created": 1695000000,
  "data": [{ "b64_json": "<base64>", "mime_type": "image/png" }]
}
```

Before serialization, returned bytes are re-validated at the HTTP boundary:
bounded size, one of PNG/JPEG/GIF/WebP by declared MIME type, and magic-byte
agreement with that MIME type. Only provider-owned HTTPS artifact sources are
fetched.

## Failure behavior

Failures are deterministic and redacted; provider URLs, prompts, credentials,
and upstream response bodies are never exposed:

| Condition                                | Status | `error.code`                     |
| ---------------------------------------- | ------ | -------------------------------- |
| Unknown model                            | 404    | `model_not_found`                |
| Model lacks image-generation capability  | 501    | `image_generation_unverified`    |
| Non-Qwen provider                        | 501    | `image_generation_not_supported` |
| Ambiguous `size` + `aspect_ratio`        | 400    | `unsupported_feature`            |
| Unsupported preset or presetless shape   | 400    | `unsupported_feature`            |
| `size`/`aspect_ratio` on another model   | 400    | `unsupported_feature`            |
| Both `aspect_ratio` and `aspectRatio`    | 400    | `invalid_request`                |
| Upstream rendering/download/timeout      | 502    | `image_generation_failed`        |

## Known Qwen Web versus Qwen Image API limitations

The current adapter reaches Qwen through the authenticated Qwen Web
**Create Image** session, not the standalone Qwen Image API. Qwen's official
OpenAI-compatible image protocol documents a top-level `model`, `prompt`, and
`size` request. The Web flow instead exposes a fixed set of aspect-ratio
presets in an interactive dropdown, which is why WM Adapter maps presets to
deterministic pixel sizes instead of forwarding arbitrary dimensions.

Specific differences verified in the live Web flow:

- The Web UI offers five numeric aspect-ratio presets and **no Auto preset**,
  even though Auto appears in Qwen's documented parameter list.
- The Web flow has no pixel-dimension input; only presets are selectable.
- Qwen's Web session uses a short-lived access token (observed lifetime: 15
  minutes) held in browser storage, renewed by the app while the browser
  session is alive. It is managed entirely in the browser profile; WM Adapter
  never reads, stores, or forwards it.

Web UI availability, the exact model selector label, account entitlement, and
the rendered pixel dimensions remain provider-side runtime facts and require a
live probe before the model is enabled. The Create Image dropdown is the
authority on which Qwen Image models an account can select.
