# WebBridgeFreeRide Architecture

## Goal
Create a local OpenAI-compatible bridge that uses free web chatbot services through browser automation.

## MVP Scope
- Provider: DeepSeek Web
- Backend: Python + FastAPI
- Automation: Playwright
- Deployment: Local Linux first
- Authentication: Local encrypted credential/session storage
- Logs: Required

## Flow
Client/Agent -> Local API -> DeepSeek Adapter -> Playwright Browser -> DeepSeek Web -> Response

## Components

### API Gateway
Receives chat requests and exposes local endpoints.

### Provider Adapter
First implementation: DeepSeekAdapter.

Future adapters may support other web chat providers.

### Browser Manager
Responsible for browser lifecycle, authentication session, and page interaction.

### Storage
Stores encrypted configuration and browser session information.

### Logging
Tracks requests, errors, provider changes, and debugging information.

## Non Goals for MVP
- Dashboard
- Usage billing
- Multi-user support
- Cloud hosting
