# MimicGate — Web-to-API Gateway for AI Agents

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
  explicit mode always takes precedence. Stage 1 defines this configuration
  contract only; lifecycle, page ownership and retry behavior remain unchanged
  until their dedicated migration stages. See [GitHub Issue #22](https://github.com/arashashrafii/mimicgate/issues/22)
  and [ADR/Issue #15](https://github.com/arashashrafii/mimicgate/issues/15)
  for the migration decisions and follow-up runtime work.
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

POST /v1/chat/completions and GET /v1/models support text messages, system/user/
assistant/tool roles, emulated function calls, tool_choice, finish_reason and
buffered SSE. DeepSeek additionally dispatches data URL images. Capabilities
are metadata extensions; unknown token limits/usage remain null. See
MIGRATION_V2.md for behavior corrections and unsupported sampling controls.

No MCP server is needed for this boundary. A client may itself expose MCP
functions as model tools; MimicGate simply preserves their schema and results.
The OpenClaw plugin is optional session cleanup, not the model transport.

The public gateway always uses the generic policy. It does not inspect message
text to identify OpenClaw, OpenCode or Hermes. Historical OpenClaw workflow
guidance remains available only to direct internal helper callers and is not
part of the agent-facing contract.

MimicGate emulates the API boundary, rather than an agent's workflow. Its
provider adapters may translate structured tools to a WebChat text marker and
translate that marker back to a standard tool call, but they do not choose,
execute or verify an agent's tools. OpenClaw-specific session cleanup remains
in the optional plugin outside the gateway request path.

Bearer authentication is optional: configure `server.api_key` and send
`Authorization: Bearer <key>`. With no key configured, existing loopback
behavior remains unchanged.

### Model response recovery
OpenClaw owns the research/action/verification loop and executes every tool.
MimicGate preserves tool calls and results across requests. Both SSE and ordinary
responses use a shared bounded recovery step: an empty response or unresolved
tool marker prompts one additional Web-chat completion with the original context.
Persistent failure returns a provider error, never fabricated success. Normal
answers are not retried. This is protocol recovery, not proof that model claims
are correct; policy text and unit tests cannot guarantee autonomous task success.

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
  failure, and intentional MimicGate cleanup are classified at this boundary.
  A Playwright transport disconnect is labeled `playwright_disconnect` unless
  the managed Chromium process has a non-zero exit status, in which case it is
  labeled `chromium_crash_or_oom` and retains the PID/status evidence.
  Intentional stop and headed-to-headless handoff use
  `initiator=mimicgate_cleanup`; external or unknown termination reports
  `LOGIN_INTERRUPTED` and cancels the watcher.

`LOGIN_INTERRUPTED` is terminal for the current login attempt. No watcher,
request retry, or browser recovery may relaunch authentication. Only the
explicit `retry_login()` operation starts a new attempt and login ID.

- Dashboard
- Usage billing
- Multi-user support
- Cloud hosting
