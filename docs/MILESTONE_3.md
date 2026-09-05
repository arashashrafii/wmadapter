# Milestone 3 Agent Client Compatibility

Implemented:

- Accepts and forwards the OpenAI-compatible request, including `tools`, `tool_choice`, tool messages, reasoning fields, and streaming.
- Adds Server-Sent Events streaming response shape for `/v1/chat/completions`. The current provider emits the completed answer as one chunk because DeepSeek DOM extraction is completion-based.
- DeepSeek Web sessions remain isolated per OpenClaw session; OpenClaw owns the
  logical session history and deletion, while the WebBridge cleanup plugin
  mirrors deletion to the matching DeepSeek Web conversation.

Validation:

- Non-streaming OpenAI-compatible response remains supported.
- Streaming returns `chat.completion.chunk` events and `[DONE]`.
