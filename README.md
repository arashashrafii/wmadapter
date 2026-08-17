# WebBridgeFreeRide

## Purpose

WebBridgeFreeRide is a proof of concept for an OpenAI-compatible local gateway that connects AI agents to browser-based free chatbot services.

Initial target: DeepSeek Web.

## Goal

Provide a local API:

```
Agent / Application
        |
        v
OpenAI compatible API (localhost)
        |
        v
Browser automation layer
        |
        v
DeepSeek Web session
```

## Proof of Concept Scope

Phase 1:

- [ ] Start local API server
- [ ] Implement OpenAI `/v1/chat/completions` compatibility
- [ ] Use Playwright browser automation
- [ ] Support persistent browser profile
- [ ] Manual login first (no password storage)
- [ ] Send prompt to DeepSeek Web
- [ ] Capture response
- [ ] Return OpenAI compatible JSON

## Non Goals

- No API key bypass
- No password storage
- No guarantee of unlimited free usage
- No dependency on undocumented APIs

## Technical Evaluation

Success criteria:

1. A local agent can call the API using an OpenAI client.
2. DeepSeek Web can answer through the browser session.
3. The workflow is stable enough for personal use.

## Future

Possible adapters:

- Gemini Web
- Claude Web
- ChatGPT Web
- Other browser-based AI assistants
