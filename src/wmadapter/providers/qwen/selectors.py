"""Qwen web selectors kept in one place for easier maintenance."""

CHAT_INPUTS = [
    'textarea[placeholder="Ask Qwen"]',
    'textarea.message-input-textarea',
]

RESPONSE_BLOCKS = [
    '.response-message-content.phase-answer',
    '.qwen-chat-message-assistant .qwen-markdown-text',
    '.qwen-chat-message-assistant .qwen-markdown',
]
