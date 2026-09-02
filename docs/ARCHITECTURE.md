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

## Non Goals for MVP
- Dashboard
- Usage billing
- Multi-user support
- Cloud hosting
