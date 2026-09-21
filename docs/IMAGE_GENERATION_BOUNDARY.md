# Image-generation integration boundary

Status: research note for Issue #82, verified against upstream sources on
2026-09-21. This note defines the WM Adapter boundary; it does not authorize
client-plugin changes.

## Executive decision

WM Adapter should expose one provider-neutral, synchronous image-generation
surface:

```http
POST /v1/images
{
  "model": "qwen-chat",
  "prompt": "a small blue square on white",
  "n": 1,
  "response_format": "b64_json"
}
```

The response contains one or more validated image byte payloads:

```json
{
  "created": 0,
  "data": [
    {"b64_json": "...", "mime_type": "image/png"}
  ]
}
```

The provider adapter owns model selection, provider-specific UI/API
translation, artifact retrieval, MIME/magic-byte validation, and safe error
mapping. A client owns its skill/tool wording, model picker, fallback policy,
workspace persistence, display, and agent wake-up/tool loop. WM Adapter must not
infer those client behaviors from message text or provider labels.

The existing implementation is intentionally narrower than the full client
surfaces: Qwen image generation is enabled only by verified configuration,
accepts one text prompt, returns base64 bytes, and rejects unsupported options.
Image input/editing remains a separate capability and must not be inferred from
image generation.

## Evidence

### Qwen Code / Qwen image tool

Verified upstream facts from [Qwen Code's `image-gen.ts`](https://github.com/QwenLM/qwen-code/blob/813232e9674d7ac1a8ef4ff80f4257ac543e8247/packages/core/src/tools/image-gen.ts)
and [model-provider configuration](https://github.com/QwenLM/qwen-code/blob/813232e9674d7ac1a8ef4ff80f4257ac543e8247/docs/users/configuration/model-providers.md):

- The built-in tool is named `image_gen`; its required argument is `prompt` and
  its optional argument is `size` in `width*height` form.
- The tool validates a total size between `512*512` and `2048*2048` pixels and
  writes a PNG workspace artifact. It may return inline image data to an
  image-capable primary model; a text-only primary model receives the saved
  path and metadata instead.
- `/model --image` and `imageModel` select the image route. A selected route
  must explicitly advertise `supportsImageGeneration` (or legacy `imageOnly`),
  and must provide an HTTPS `baseUrl` plus a non-empty environment-key name.
- The route's configured model is sent to the provider. Qwen Code's transport
  forwards the optional size as a provider parameter; model-specific size
  semantics therefore remain provider-owned.

The [Qwen Cloud image-generation documentation](https://docs.qwencloud.com/developer-guides/image-generation/text-to-image)
confirms why size cannot be treated as a universal promise: models differ in
accepted ranges, presets, defaults, and aspect-ratio rules. The Web UI's
“Create Image” entry point is a client workflow, not a provider-neutral API
contract.

### OpenClaw

Verified upstream facts from [OpenClaw's image-generation guide](https://github.com/openclaw/openclaw/blob/585312d8ba73fb53ee870012066274917f411ede/docs/tools/image-generation.md),
[runtime types](https://github.com/openclaw/openclaw/blob/585312d8ba73fb53ee870012066274917f411ede/src/image-generation/runtime-types.ts),
and [provider request types](https://github.com/openclaw/openclaw/blob/585312d8ba73fb53ee870012066274917f411ede/src/image-generation/types.ts):

- The client tool is `image_generate`, and it may run asynchronously with a
  background task and completion wake-up.
- Selection order is per-call model override, configured primary image model,
  configured fallbacks, then provider auto-detection when no explicit list is
  configured. A per-call model override is exact and does not continue through
  other candidates.
- The client-facing request includes prompt, model, count, size, aspect ratio,
  resolution, quality, output format, background, optional reference images,
  timeout, and provider options. Providers declare which of these they support;
  unsupported or normalized hints are reported by the client.
- The provider result is a non-empty array of binary image assets with MIME
  type and optional metadata. The client, not WM Adapter, owns fallback,
  persistence, attachment delivery, and user-facing completion behavior.

### OpenCode

OpenCode's current `dev` source contains a narrowly scoped
[`openai.image_generation` provider tool](https://github.com/anomalyco/opencode/blob/c10134729dd2ce00beb18604ec91f10319f59a78/packages/core/src/github-copilot/responses/tool/image-generation.ts),
and its [tool preparation](https://github.com/anomalyco/opencode/blob/c10134729dd2ce00beb18604ec91f10319f59a78/packages/core/src/github-copilot/responses/openai-responses-prepare-tools.ts)
maps it to the OpenAI Responses `image_generation` tool. This is part of the
GitHub Copilot/OpenAI Responses integration, not a generic OpenCode image model
picker or a provider-neutral OpenCode image route. The schema supports provider
fields such as model, size, quality, output format, background, and input mask;
the tool result is a provider-executed base64 `result` string.

The normal OpenCode agent surface currently has image attachment normalization
and image input handling, but no generic `image_generate` workflow. WM Adapter
should therefore preserve the ordinary OpenAI-compatible route and must not add
OpenCode-specific tool injection.

## Facts, unknowns, and assumptions

| Classification | Finding |
|---|---|
| Verified | Qwen Code selects a configured image route and accepts prompt plus optional size. |
| Verified | OpenClaw owns image tool selection, fallback, provider hint normalization, and async delivery. |
| Verified | OpenCode's discovered image-generation schema is scoped to its OpenAI Responses integration. |
| Verified in WM Adapter | `/v1/images` returns validated base64 bytes only when Qwen image generation is enabled by verified configuration. |
| Unknown | Qwen Web account, region, quota, entitlement, and UI rollout behavior; none should be inferred from a label or failed probe. |
| Unknown | A stable Qwen Web artifact URL or public UI protocol suitable for direct client use. |
| Assumption | A future provider-neutral size field can exist only when capability metadata states its accepted semantics; until then, reject it explicitly. |
| Assumption | Image-generation output should remain bytes/metadata at the gateway boundary; URL fetching, workspace files, and attachment delivery stay with the client or provider adapter. |

## Proposed capability contract

Keep `GET /v1/models` capability metadata truthful and independent:

- `image_generation: true` only when the selected provider can complete the
  configured image-generation probe and the adapter can validate the returned
  bytes.
- `image_editing: true` only after an independent input-image/edit probe;
  image generation does not imply it.
- `image_input: true` only after an independent vision/input probe; image output
  does not imply it.
- If a future version supports optional controls, advertise them as explicit
  provider capability metadata (for example supported count, size syntax, or
  output formats). Do not silently drop a requested control.

At the HTTP boundary, the stable minimum is `model`, non-empty `prompt`,
`n=1`, and `response_format=b64_json`. A future extension may add `n` and
provider-neutral geometry/output hints, but each field must have validation,
capability metadata, and deterministic unsupported behavior. The gateway should
never return provider URLs, execute provider artifacts, fabricate a result, or
turn an image-generation call into a chat/tool loop.

## Minimal client configuration

Clients need only a normal OpenAI-compatible base URL and the model exposed by
`GET /v1/models`. They should not add Qwen- or DeepSeek-specific code to WM
Adapter. A client may add its own image tool that calls `/v1/images`, but it
must treat the capability metadata and HTTP errors as authoritative.

For the current OpenClaw adapter, the client-specific configuration is an
`wmadapter` image provider pointing at the gateway's `/v1` base URL and the
`qwen-chat` model. The adapter intentionally rejects size, aspect ratio,
quality, editing, and reference-image options until WM Adapter advertises a
verified provider-neutral contract for them.

## Deterministic fixture and test recommendations

`tests/fixtures/image_generation_boundary.json` is a sanitized, network-free
cassette for the contract boundary. It uses a tiny PNG signature plus the word
`fixture`; it contains no credentials, cookies, signed URLs, provider payloads,
or account data.

Recommended deterministic coverage:

1. Validate the successful `b64_json` response shape, MIME type, and base64
   decoding without making a provider request.
2. Assert that unverified capability returns `501 image_generation_unverified`
   and no image data.
3. Assert that unsupported controls, empty prompts, non-`b64_json` formats,
   and unknown models fail before the provider is called.
4. Feed mismatched MIME/magic bytes, disallowed URLs, empty bytes, and an
   over-limit artifact to the provider-artifact validator; no bytes should
   cross the boundary.
5. Keep client adapter tests separate: one fixture should assert that the
   client sends only the stable minimum request and preserves the returned
   binary asset, while client fallback/async/persistence behavior belongs in
   the client project.

These tests are intentionally fixture-driven. Live Qwen UI checks remain
capability evidence, not a substitute for deterministic HTTP contract tests.
