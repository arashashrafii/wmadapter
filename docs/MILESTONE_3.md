# Milestone 3 Agent Client Compatibility

Implemented:

- Accepts common OpenAI chat completion fields: `temperature`, `top_p`, `max_tokens`, `user`, and `stream`. Unsupported sampling fields are accepted for client compatibility and ignored by the browser-backed provider.
- Adds Server-Sent Events streaming response shape for `/v1/chat/completions`. The current provider emits the completed answer as one chunk because DeepSeek DOM extraction is completion-based.
- Adds `conversation_id` to keep separate browser pages for sequential agent conversations in one process.

Validation:

- Non-streaming OpenAI-compatible response remains supported.
- Streaming returns `chat.completion.chunk` events and `[DONE]`.
