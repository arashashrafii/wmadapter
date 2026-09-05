# WebBridgeFreeRide Architecture

## Goal
Create a local OpenAI-compatible bridge for free web chat services while
preserving OpenClaw's native agent loop as far as the web model permits.

## MVP Scope
- Provider: DeepSeek Web
- Backend: Python + FastAPI
- Automation: Playwright
- Deployment: Local Linux first
- Authentication: Local encrypted credential/session storage
- Logs: Required

## Flow
OpenClaw Agent -> Local OpenAI API -> Playwright -> DeepSeek Web -> text/marker parser -> OpenClaw

## Components

### API Gateway
Receives chat requests and exposes local endpoints.

### Provider Adapter
First implementation: DeepSeekAdapter.

Future adapters may support other web chat providers.

### Browser Manager
OpenClaw owns tool execution. The bridge only translates an allowlisted textual
tool marker into the OpenAI-compatible `tool_calls` shape.

### Storage
Stores browser profiles and non-secret application configuration.

### Logging
Tracks requests, errors, provider changes, and debugging information.

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
