from ..webchat_adapter import WebChatTextAdapter
from ..recovery import ToolCallRecovery


class QwenTextAdapter(WebChatTextAdapter):
    """Qwen Web text dialect; selectors and DOM remain in chat.py."""

    def __init__(self, recovery_timeout_ms: int = 120000):
        super().__init__(ToolCallRecovery(timeout_ms=recovery_timeout_ms))
