# Web Model Adapter — Web-to-API Gateway for AI Agents

## Goal
An AI Compatibility Gateway: OpenCode, Hermes and OpenClaw consume standard
Chat Completions while provider adapters communicate with WebChat pages.
The client agent owns tool execution and sends the result on its next request.

## Runtime boundary

- main:app is the application; api/server re-exports it for compatibility.
- api/validation validates the HTTP subset before any browser request.
- providers/contract defines external DTOs plus provider-independent canonical
  messages, requests, tools and results. main converts the DTO once at the boundary.
- ChatProvider.infer is the V2 interface. Its default legacy adapter serializes
  messages/tools, calls the unchanged complete(prompt)->str method, then
  normalizes the Web response. V1 subclasses remain valid.
- providers/protocol holds shared prompt, marker parsing and recovery behavior.
- providers/webchat_adapter.py is the provider translation interface; DeepSeek
  and Qwen select explicit provider-owned protocol adapter classes.
- providers/recovery.py owns provider-neutral recovery. The old protocol
  function remains only as a compatibility wrapper for legacy helper callers.
- service.py owns DeepSeek/Qwen lifecycle, locks, retries and conversations.
  Provider chat/login/selectors retain site-specific DOM code; browser/elements
  contains the common visible-element lookup and BrowserManager owns profiles.
- Browser configuration declares browser.mode as managed (default) or cdp. A
  legacy config with browser.cdp_endpoint and no mode migrates to cdp; an
  explicit mode always takes precedence at runtime. Managed mode always launches
  and owns its persistent context, while CDP mode only attaches and never
  acquires the profile lock or closes the user's context. Provider status reports
  the resolved mode and ownership. See [GitHub Issue #22](https://github.com/arashashrafii/wmadapter/issues/22)
  and [ADR/Issue #15](https://github.com/arashashrafii/wmadapter/issues/15)
  for the migration decisions.
- Managed authentication uses one canonical absolute profile and executable
  across a sequential headed-to-headless handoff. The managed BrowserManager
  holds an exclusive profile lock, releases it only after the headed context
  and Playwright handle stop, then launches headless and runs an authentication
  probe. A failed launch or probe stops the failed context and attempts to
  restore headed mode on the same profile; the resulting error identifies
  whether restoration succeeded. CDP mode remains a separate attach path and
  never participates in this lock or handoff and never closes the user browser.
- BrowserManager page ownership is provider-scoped. DeepSeek and Qwen claim
  only pages matching their own WebChat origin; unrelated tabs and pages
  claimed by the other provider are skipped, and a close event releases the
  claim. Managed contexts are manager-owned; CDP contexts remain user-owned,
  so provider selection never closes unrelated tabs or the user's browser.
- Each provider service applies the shared browser page policy: `max_pages`
  defaults to 8 unique Gateway-owned pages and `idle_timeout_ms` defaults to
  300000. Setting either field to `null` explicitly disables that limit or
  idle cleanup. Cleanup removes aliases, releases local ownership and closes
  only the local page; it never invokes remote conversation deletion. Active
  or in-flight pages are protected, and a full cap returns a capacity error.
- Model discovery comes from registered provider model_ids and capabilities.
  GPT Web/OX Alpha are extension targets only; no dummy adapters are registered.

## Contract scope

POST /v1/chat/completions, POST /v1/completions, POST /v1/embeddings, POST /v1/images, and GET /v1/models support text messages, system/user/
assistant/tool roles, emulated function calls, tool_choice, finish_reason and
buffered SSE. DeepSeek retains bounded data-URL upload code for verified use,
but image input is not advertised until live model/UI verification establishes
observable vision support. Capabilities are metadata extensions; unknown token
limits/usage remain null. See
MIGRATION_V2.md for behavior corrections and unsupported sampling controls.

The legacy `/v1/completions` route accepts a string `prompt`, model, user, and
stream flag, translates the prompt to one canonical user message, and returns
the legacy text-completion envelope. Unsupported legacy fields and non-string
prompts are rejected explicitly; token arrays, logprobs, echo, suffix, and
other provider-specific options are not silently emulated.

The embeddings route validates string input and model selection but returns
`501 embeddings_not_supported`; current web providers expose no verified
embedding capability, and the gateway never fabricates deterministic vectors.

Sampling and stop controls are type/range checked at the request boundary.
Current web adapters do not expose deterministic control over temperature,
top_p, token limits, penalties, seed, or stop sequences, so those values are
rejected explicitly. `n=1` and streamed `stream_options.include_usage` are the
only supported controls; Responses applies the same truthful rejection policy.

Model discovery includes the registered provider identity and a separate limits
object. Gateway character limits are reported when configured; upstream context
and output-token limits remain `null` when unverified. Usage is never estimated
from message text or response length. Chat Completions and Responses expose
provider usage only when all three token counts are observed, non-negative
integers, and internally consistent; otherwise they return `usage: null`.
When streamed usage is requested, the final empty-choice chunk is emitted with
that same observed usage or `null`, and contains no provider-private fields.

Tool schemas are normalized at the Chat Completions boundary. The supported
form is an OpenAI function tool with a bounded name, optional string
description/boolean strict flag, and object JSON-schema parameters. Custom tool
forms and parallel execution are rejected because the web adapters only
provide serial, emulated function-call recovery. `auto`, `none`, `required`,
and a specific supplied function are validated; malformed calls and invalid
JSON arguments are rejected. Responses tool fields remain explicitly
unsupported, and no tool is executed by the gateway.

The images route validates a non-empty text prompt and model selection but
returns `501 image_generation_not_supported`; current web providers expose no
verified image-generation path, and the gateway never fabricates image data.

Audio speech, transcription, translation, and Realtime routes validate their
request shapes but return `501` (`audio_not_supported` or
`realtime_not_supported`). Their capability flags are false because the web
providers expose no verified audio or realtime browser path; no audio,
transcript, vectors, or session data is fabricated or retained.

Video/media parts and Files/PDF routes are similarly explicit: capability flags
are false, unsupported content parts return safe validation errors, and file
metadata/upload/delete routes return `501 files_not_supported`. No file store,
PDF parser, or provider-understanding claim is introduced.

Batch create/list/retrieve/cancel routes validate the supported request shape
where applicable but return `501 batches_not_supported`. Batch capability is
false because the gateway has no verified asynchronous provider job path or
persistent job store.

Only providers listed in `providers.enabled` are started at application launch;
an enabled-provider startup failure leaves the application running with that
provider not ready while other enabled providers are still attempted. Qwen's
observable contract is text-only; multimodal support is not advertised.

No MCP server is needed for this boundary. A client may itself expose MCP
functions as model tools; Web Model Adapter simply preserves their schema and results.
The OpenClaw plugin is optional session cleanup, not the model transport.

Compatibility summary: `/health`, `/ready`, `/props`, `/v1/models`, Chat
Completions, the documented legacy Completions subset, text-only non-streaming
Responses, and the OpenCode translation route are implemented. OpenClaw uses
the standard Chat Completions route and executes returned tools client-side.
Embeddings, image generation, audio, Realtime, Files/PDFs, and Batches are
validated surfaces with explicit `501` unsupported responses; they do not
create fake output, storage, or asynchronous jobs. Image, video, audio, file,
and PDF input is rejected when no verified provider capability exists.

The public gateway always uses the generic policy. It does not inspect message
text to identify OpenClaw, OpenCode or Hermes. Historical OpenClaw workflow
guidance remains available only to direct internal helper callers and is not
part of the agent-facing contract.

Request sizing uses `limits.context_budget_chars` (default 24000) with optional
exact model or provider overrides in `limits.context_budget_profiles`. Small
prompts are passed unchanged. Oversized histories receive one pre-submit
compaction consisting of a metadata-only state ledger and recent message
window; an oversized current message fails with `context_length_exceeded`.
Only one provider submission is allowed after compaction, and uncertain or
completed submissions are never replayed. Sanitized request metrics contain
provider/model, counts, prompt length, a fingerprint, and the compaction flag;
they never contain prompt text, credentials, or transcripts.

The OpenCode route may accept a positive `max_tokens` as a client-requested
budget for local context/headroom diagnostics. The value is removed before the
canonical provider request is built and is never serialized or forwarded to a
web provider. Shared Chat Completions and OpenClaw continue to reject sampling
controls with HTTP 400; zero, negative, non-integer, or conflicting budgets are
invalid. Unknown `max_output_tokens` remains metadata-only.

`/health` reports gateway process health only. `/ready` reports authenticated
provider readiness and may return 503; readiness failure is not conflated with
gateway process failure.

Web Model Adapter emulates the API boundary, rather than an agent's workflow. Its
provider adapters may translate structured tools to a WebChat text marker and
translate that marker back to a standard tool call, but they do not choose,
execute or verify an agent's tools. OpenClaw-specific session cleanup remains
in the optional plugin outside the gateway request path.

Bearer authentication is optional: configure `server.api_key` and send
`Authorization: Bearer <key>`. With no key configured, existing loopback
behavior remains unchanged.

### Model response recovery
OpenClaw owns the research/action/verification loop and executes every tool.
Web Model Adapter preserves tool calls and results across requests. Both SSE and ordinary
responses use a shared bounded recovery step: an empty response or unresolved
tool marker prompts one additional Web-chat completion with the original context.
Persistent failure returns a provider error, never fabricated success. Normal
answers are not retried. This is protocol recovery, not proof that model claims
are correct; policy text and unit tests cannot guarantee autonomous task success.

Recovery diagnostics are bounded and redacted: they contain only a fixed reason
code, response length, SHA-256 fingerprint, and outcome. Reasons distinguish
`initial_empty`, `initial_unresolved_marker`, `repair_empty`,
`repair_unresolved_marker`, and `repair_invalid_tool_call` (plus successful
bypass/repair outcomes); prompts, responses, credentials, and PII are never
logged. One oversized repair context is compacted before the single repair
request. Streamed provider completion failures/timeouts retain their provider
error codes, while a failed protocol repair emits `protocol_recovery_failed`
and still terminates with exactly one `[DONE]`.

Recovery also catches a third identical non-process tool call after two identical
results in the current user turn. It asks the model for a different approach once,
then fails explicitly if repetition persists. Process polling is exempt. This
conservative guard may also stop intentional repeated non-process observations;
it does not classify every error or guarantee that a changed strategy is correct.

## Non Goals for MVP
- Browser authentication lifecycle diagnostics are emitted by `BrowserManager`.
  Each record contains `event_name`, UTC `timestamp`, `provider`,
  `login_attempt_id`, `browser_generation`, `page_count`, `auth_state`,
  `initiator`, `reason`, `pid`, and nullable `exit_status`. Page closure, page
  crash, context closure, Playwright/browser disconnect, display/session launch
  failure, and intentional Web Model Adapter cleanup are classified at this boundary.
  A Playwright transport disconnect is labeled `playwright_disconnect` unless
  the managed Chromium process has a non-zero exit status, in which case it is
  labeled `chromium_crash_or_oom` and retains the PID/status evidence.
  Intentional stop and headed-to-headless handoff use
  `initiator=wmadapter_cleanup`; external or unknown termination reports
  `LOGIN_INTERRUPTED` and cancels the watcher.

`LOGIN_INTERRUPTED` is terminal for the current login attempt. No watcher,
request retry, or browser recovery may relaunch authentication. Only the
explicit `retry_login()` operation starts a new attempt and login ID.

- Dashboard
- Usage billing
- Multi-user support
- Cloud hosting
