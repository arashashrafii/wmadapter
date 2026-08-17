# Milestone 1 End-to-End Test Plan

Goal: Validate:

Client -> Local API -> DeepSeek Adapter -> Browser -> DeepSeek Web -> Response

Tests:
1. Start local API.
2. Launch Playwright browser profile.
3. Verify DeepSeek login state.
4. Send test prompt.
5. Extract generated response.
6. Return API response.

Known limitation: selectors may require updates when DeepSeek UI changes.
