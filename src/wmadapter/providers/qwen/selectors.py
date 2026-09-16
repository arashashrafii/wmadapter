"""Qwen web selectors kept in one place for easier maintenance."""

CHAT_INPUTS = [
    'textarea[placeholder="Ask Qwen"]',
    'textarea.message-input-textarea',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"]',
]

RESPONSE_BLOCKS = [
    '.response-message-content.phase-answer',
    '.qwen-chat-message-assistant .qwen-markdown-text',
    '.qwen-chat-message-assistant .qwen-markdown',
]

# Generated artifacts are scoped to the conversation container and restricted
# to provider-owned hosts. Do not broaden this selector to arbitrary images.
IMAGE_ARTIFACTS = [
    "#chat-message-container img[src^='https://cdn.qwenlm.ai/']",
    "#chat-message-container img[src^='https://img.alicdn.com/']",
]
