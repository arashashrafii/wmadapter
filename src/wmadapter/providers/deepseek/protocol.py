from ..webchat_adapter import WebChatTextAdapter
from ..recovery import ToolCallRecovery


class DeepSeekTextAdapter(WebChatTextAdapter):
    """DeepSeek Web text dialect; selectors and DOM remain in chat.py."""

    def __init__(self):
        super().__init__(ToolCallRecovery())
