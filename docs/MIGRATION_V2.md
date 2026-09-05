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
