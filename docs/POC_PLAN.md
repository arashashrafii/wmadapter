# Web Model Adapter Proof of Concept Plan

## Goal
Validate whether a browser-backed OpenAI-compatible gateway can use DeepSeek Web as a provider.

## Scope
- Local OpenAI-compatible API
- Browser automation provider
- Persistent authenticated browser session
- DeepSeek Web adapter
- Single chat completion test

## Architecture
Client/Agent -> Local API -> Provider Adapter -> Browser Automation -> DeepSeek Web -> Response Parser -> OpenAI Response

## Validation Criteria
1. User can authenticate without storing passwords.
2. API accepts OpenAI chat completion format.
3. Prompt can be sent through browser automation.
4. Response can be returned to an OpenAI-compatible client.
5. Failure cases are documented.

## Implementation Order
1. Study existing web-model-bridge implementations.
2. Create minimal FastAPI gateway.
3. Add Playwright browser session manager.
4. Implement DeepSeek adapter.
5. Test with OpenAI SDK.
6. Evaluate reliability before expanding.
