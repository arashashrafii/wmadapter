# Gateway V2 audit and migration

Baseline: d34c7af, 2026-09-05. Clean checkout; 86 unittest tests passed.

## Audit before implementation

- Entrypoint: CLI runs main:app. main.py (890 lines) owns request schemas,
  prompt policies, renderer recovery, tool parsing, routes and SSE. Real
  chat/models endpoints already exist. api/server.py is a disconnected stub
  that returns fabricated completion text; it must alias the real application.
- ChatProvider: lifecycle plus complete(prompt, conversation_id)->str and
  buffered stream_complete. Router dispatches names/prefixes and silently
  routes unknown names to the default. No structured provider contract.
- service.py: DeepSeek and Qwen own browser lifecycle, locks, restart retry,
  conversation pages and auth. DeepSeek supports data image uploads; Qwen
  does not. Both return whole DOM text. DeepSeek-reasoner is only an alias:
  no explicit model/reasoning selector is wired.
- BrowserManager uses persistent Playwright profiles. Provider chat/login and
  selector modules correctly isolate DOM differences. Duplicate browser.py,
  deepseek.py and deepseek/adapter.py are old placeholders, not runtime paths.
- DOM completion uses text stability, including partial output at timeout;
  it cannot establish token counts, token limits or a trustworthy length finish.
  Qwen navigates home on every authentication check, resetting conversation.
- Tool protocol is text emulation with allowlisting and one recovery attempt.
  The caller executes tools. Existing prompt policies are strongly OpenClaw
  specific. Assistant call IDs and mixed assistant content are lost in prompts.
  required/named tool_choice is ignored; only none is supported.
- SSE buffers everything before emitting content and finish. It lacks an
  initial assistant role; title requests ignore streaming. Usage zero and
  /props context 128000 are unsupported estimates. Errors use FastAPI detail.
- Config validates basic types but enabled is used for readiness only. API
  binds loopback by default, has no authentication or multi-user isolation.
  Keep existing config/session compatibility; do not expose publicly.
- OpenClaw plugin binds and deletes sessions; it is not a model transport or
  MCP server. It contains host-specific CLI paths and a different default
  port; remote deletion verification is weak. Preserve cleanup behavior in
  this migration; no destructive live testing.
- Tests cover helper behavior, fake DOM deletion and recovery (86). They do
  not prove HTTP integration, actual client acceptance or current Web DOM.

## Planned incremental changes

1. Record audit and baseline (this commit).
2. Extract shared schemas/protocol with main imports preserved. Add structured
   request/result/capabilities and a legacy text adapter; retain complete and
   stream_complete. Migrate DeepSeek first, then Qwen; keep DOM code isolated.
3. Use V2 at HTTP boundary, share real app with api/server, add validation,
   standard errors, model metadata and consistent buffered SSE.
4. Add HTTP contract and tool-result round-trip tests; document consumer
   configuration, capability limits and measured verification status.

Run the suite before/after each commit. Revert commits in reverse order for
rollback; no config/profile migrations or external publishing required.

## Compatibility decisions

Preserve legacy class methods, main helper imports, model aliases, session
headers and cleanup routes. Unknown HTTP models become explicit 404 errors
instead of silently selecting another Web model. Tool calling is emulated,
not native; required/named choices must either produce a matching call or
fail. Unknown context/output limits and usage must not be fabricated. SSE
remains buffered because DOM adapters do not expose trustworthy token deltas.
No MCP dependency and no fake GPT Web or OX Alpha implementation.

## Implementation outcome

The contract migration is complete through steps 1-10: V1 behavior is
documented, contract/error tests exist, reasoning metadata and optional bearer
auth are present, public routing has one strict resolver, canonical messages and
tools are introduced, the API converts into them, ChatProvider accepts them via
ProviderRequest, legacy text completion remains available, and DeepSeek/Qwen
use explicit protocol adapter classes. Browser behavior, MCP, GPT/OX and true
incremental streaming were intentionally unchanged.

The next staged boundary is `ToolCallRecovery`; provider adapters now call it
instead of importing the recovery function directly. The legacy implementation
remains delegated until its policy and retry rules have dedicated tests.

- c6c97bb records the baseline and plan.
- defa0c1 adds contract.py, the shared legacy protocol and DeepSeek V2 support;
  89 tests pass. main helper imports remain available.
- ecb13c8 adds Qwen capability metadata, preserves its authenticated page and
  extracts only the duplicated visible-element lookup; 91 tests pass.
- fc2c7db switches HTTP to V2, aliases api/server to the real app, validates
  requests and supplies model capabilities, standard errors and buffered SSE;
  101 tests pass.
- Final verification adds real SDK transport smoke scripts, malformed-input
  coverage and timeout regressions; 106 tests pass.

The full protocol extraction is mechanical relocation, not a rewrite of DOM
or OpenClaw policies. complete(prompt)->str and stream_complete remain callable.
Unknown HTTP models now fail 404; invalid request bodies fail 400 using an
OpenAI error envelope. Unmeasured usage/context values are null, not zero or
128000. These deliberate corrections may affect callers relying on old values.
At a DOM timeout, partial output now raises instead of being reported as a
successful completion. Existing bounded provider retries still apply.

SSE is role -> one buffered content/tool delta -> finish -> optional usage ->
[DONE]. Errors after headers appear as an error event then [DONE]. There is no
incremental token stream or fabricated length finish. Model metadata describes
this explicitly. stream_infer is an additive buffered provider interface;
HTTP uses infer for one complete normalized result.

## Deferred risks, without claims of completion

- Current live DeepSeek/Qwen DOM and real agent workflows still need the
  authenticated checks in CLIENT_COMPATIBILITY.md. Tool-call decision quality
  is not guaranteed by text emulation or the HTTP contract.
- Existing OpenClaw-specific prompt policies, renderer repair and tool-specific
  argument normalization remain for compatibility. A configurable neutral
  policy and stricter JSON-schema validation require separate migration work.
- Only one outgoing tool call is emulated per turn. Multiple tool results can
  be preserved in input; parallel generation is advertised false. Tools are
  never executed by the gateway. MCP is not needed here.
- Sampling, max_tokens, stop, response_format and reasoning controls remain
  accepted legacy fields but are not enforced by the Web adapters. Models
  expose sampling_controls=false and unknown token limits. deepseek-reasoner
  remains a legacy alias, not a guarantee of a selected reasoning mode.
- Browser completion is still based on text stability and can stop too early
  during pauses before the timeout. Retries may resubmit an ambiguous request.
- Conversation fallback hashes the first user text; identical prompts may
  share state. Use explicit unique conversation_id/session headers. Cleanup
  races, resource eviction and multi-user isolation remain out of scope.
- enabled retains its old readiness-only meaning. /models lists registered
  adapters, not authenticated sessions; use /ready for current availability.
- Plugin host-specific paths/port and legacy unused placeholders remain;
  no destructive cleanup test or profile migration was done.
- New GPT Web/OX Alpha adapters must supply tested browser code, model_ids,
  capabilities, lifecycle and complete or a native infer override. None is
  registered or presented as functional in this change.

The final live check found Qwen authenticated and selector-ready without
sending a message. DeepSeek could not be checked because its persistent profile
was locked by an existing Chrome process; this is an environment blocker, not
evidence of provider success.
