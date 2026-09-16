# Qwen Web capability baseline

Status: baseline plus the previously recorded image-generation verification for
Issue #79. Issue #69's audio, voice, image-understanding/editing, and
long-video workflows were not live-verified in the current environment. Live
verification must be repeated when the Qwen Web UI changes.

## Entry points and session behavior

- Entry point: Qwen Web chat at `https://chat.qwen.ai/`.
- Authentication: browser-owned session, with normal Qwen login or Google
  authentication completed by the user in the opened browser. Credentials are
  never accepted or stored by Web Model Adapter.
- Local profile: `.wmadapter-profile/qwen` (managed mode), or an explicitly
  user-launched Chrome CDP session (CDP mode).
- Chat flow: open the chat entry point, confirm that the composer is editable,
  enter one user message, submit with Enter, and wait for a stable rendered
  response. CAPTCHA/challenge screens remain user-driven.
- Observed adapter entry point: `QwenTextAdapter`, using the Qwen chat
  selectors in `src/wmadapter/providers/qwen/`.

## Capability matrix

| Capability | Status | Evidence / boundary |
|---|---|---|
| Text chat | Verified | Authenticated Qwen Web session with the visible `Qwen3.7-Plus` model returned `QWEN_OK` from QW-T01; see current live evidence below. |
| Web search / extraction | Unsupported at adapter boundary | No deterministic live evidence or provider-neutral extraction contract is available; search results are not inferred from answer text. |
| Code interpreter / data analysis / charts | Unsupported at adapter boundary | The gateway does not execute provider code. A chart or file is only representable as validated metadata after a provider-specific live verification. |
| Qwen Studio Artifacts | Unsupported at adapter boundary | No live observation establishes safe preview/update/export semantics. HTML/SVG is never rendered or executed by the gateway. |
| Reasoning text | Blocked pending live observation | Do not advertise a reasoning model or expose hidden-thought content based on labels. |
| Image generation | Verified separately | QW-M01 evidence belongs to Issue #79; it is not evidence for audio, voice, image understanding/editing, or video understanding. |
| Image understanding/editing | Unsupported at adapter boundary | No live UI observation establishes image upload/grounded understanding or an edit artifact; the adapter advertises `image_input=false`, `image_editing=false` and rejects image parts. |
| Video generation | Blocked | No live UI observation establishing a Qwen video agent. |
| Image input | Unsupported at adapter boundary | No live UI observation establishes upload/vision support; image parts are rejected and `image_input=false`. |
| Video input / long-video understanding | Unsupported at adapter boundary | No live UI observation establishes video-input support; video parts are rejected and `video_input=false`. Duration/size limits are unknown. |
| Audio understanding, speech input/output, and voice conversation | Unsupported | `/v1/audio/*` and `/v1/realtime` validate request shape, then return explicit `501` errors; no audio bytes, transcript, speech artifact, or realtime session is fabricated. |
| Files/PDFs | Unsupported | File route is contract-validated and file/PDF handling is explicitly unsupported. |
| Streaming | Unsupported by Qwen baseline | The browser adapter returns a completed rendered answer; no token-level upstream stream has been observed. |
| Usage/token limits | Unknown | Qwen Web limits and usage are not exposed by the verified adapter boundary. |
| Availability, quota, model/agent, region restrictions | Unknown | No provider-reported availability, quota, regional restriction, or account entitlement is exposed by the verified adapter boundary. |

## Safe metadata contract

The provider-neutral result contract can carry validated `citations`,
`generated_files`, `artifacts`, and redacted `events` metadata. These are
references only: URLs must use HTTPS, file/artifact payloads are not carried,
downloaded, rendered, or executed by the gateway, and progress events are not
promoted into final assistant text. Qwen-specific adapters must additionally
allowlist provider-owned hosts before populating a reference. Empty metadata
and the explicit unsupported responses above are the truthful result when a
workflow is not verified.

“Blocked” means the capability requires a live UI observation before a truthful
claim can be made. “Unsupported” means the current gateway deliberately rejects
the capability; it is not a claim about every Qwen product or account.

## Reproducible manual/live cases

Run these against a user-owned, already authenticated Qwen browser session.
Record date, URL, selected agent/model, account state, and the visible result;
never record credentials, tokens, or profile contents.

| Case | Procedure | Passing evidence |
|---|---|---|
| QW-T01 text | Open Qwen Web; send `Reply only with: QWEN_OK`. | One visible answer exactly containing `QWEN_OK`; no login/challenge. |
| QW-T02 reasoning | Select each visibly offered reasoning agent/model; ask `Return only: REASONING_OK`. | Agent name, response, and whether reasoning is exposed are captured from the UI. |
| QW-M01 image | Select `Create Image`, confirm the displayed agent/model and ratio, submit a benign prompt, and wait for the final rendered image. | A final image is visible and `/v1/images` returns validated image bytes; otherwise mark blocked. |
| QW-M02 video | Inspect composer attachments/tools and any agent picker. | A working video-generation flow is observed end-to-end; otherwise mark blocked. |
| QW-M03 image input | Attach a benign local test image and ask for a one-line description. | Successful upload and grounded description; otherwise mark blocked. |
| QW-M04 video input | Attach a benign short test video if the UI permits. | Successful upload and grounded response; otherwise mark blocked. |
| QW-M05 image edit | Submit a benign image edit with an explicit prompt. | Successful edit artifact tied to the input image; otherwise mark blocked. |
| QW-A01 audio | Inspect input/output controls and attempt only if the UI visibly supports it. | Working audio input understanding and/or speech output evidence, or unsupported/blocked with the visible reason. |
| QW-A02 voice conversation | Inspect microphone, turn-taking, and audio reply controls; attempt only if visibly supported. | A completed voice turn with visible transcript/audio response, or unsupported/blocked with the visible reason. |
| QW-F01 file/PDF | Inspect upload controls and attempt a benign test PDF if offered. | Working file/PDF evidence, or unsupported/blocked with the visible reason. |
| QW-S01 streaming | Observe answer rendering while sending QW-T01. | Incremental token delivery is visible and reproducible; otherwise unsupported by this baseline. |
| QW-L01 limits | Inspect account/model/region usage and send bounded requests only. | Provider-reported availability/quota/model/region/size limit with its UI location; otherwise unknown. |

## Current live evidence

On 2026-09-15, Qwen Studio loaded at `https://chat.qwen.ai/` in an already
authenticated browser-owned session. The visible model was `Qwen3.7-Plus`; the
composer accepted `Reply only with: QWEN_OK`, and the rendered answer stabilized
as `QWEN_OK`. No credentials were recorded or stored by the adapter. This
verifies QW-T01 only. QW-M01 is tracked as separate Issue #79 evidence; QW-A01,
voice conversation, image understanding/editing, and QW-M04 long-video
understanding remain unverified in this environment and are not advertised.
No availability, quota, model, region, or size limit is inferred from
documentation or from an unsuccessful probe.

## Follow-up implementation issues

- #63: Qwen text/reasoning implementation, dependent on QW-T01/QW-T02 live evidence.
- #64: Qwen image generation, dependent on QW-M01 evidence.
- #65: Qwen video generation, dependent on QW-M02 evidence.
- #66: Qwen image/video input, dependent on QW-M03/QW-M04 evidence.
- #67: Other Qwen media capabilities, dependent on QW-A01/QW-F01/QW-S01/QW-L01 evidence.
