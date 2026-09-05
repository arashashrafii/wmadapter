# WebBridgeFreeRide Architecture

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
- Model discovery comes from registered provider model_ids and capabilities.
  GPT Web/OX Alpha are extension targets only; no dummy adapters are registered.

## Contract scope

POST /v1/chat/completions and GET /v1/models support text messages, system/user/
assistant/tool roles, emulated function calls, tool_choice, finish_reason and
buffered SSE. DeepSeek additionally dispatches data URL images. Capabilities
are metadata extensions; unknown token limits/usage remain null. See
MIGRATION_V2.md for behavior corrections and unsupported sampling controls.

No MCP server is needed for this boundary. A client may itself expose MCP
functions as model tools; WebBridge simply preserves their schema and results.
The OpenClaw plugin is optional session cleanup, not the model transport.

The public gateway always uses the generic policy. It does not inspect message
text to identify OpenClaw, OpenCode or Hermes. Historical OpenClaw workflow
guidance remains available only to direct internal helper callers and is not
part of the agent-facing contract.

Bearer authentication is optional: configure `server.api_key` and send
`Authorization: Bearer <key>`. With no key configured, existing loopback
behavior remains unchanged.

### Model response recovery
OpenClaw owns the research/action/verification loop and executes every tool.
WebBridge preserves tool calls and results across requests. Both SSE and ordinary
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
- Dashboard
- Usage billing
- Multi-user support
- Cloud hosting
