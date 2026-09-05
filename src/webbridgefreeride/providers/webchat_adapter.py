from __future__ import annotations

from typing import Any

from .protocol import _image_attachments, _prompt, _resolve_web_answer


class WebChatTextAdapter:
    """Provider-side translation boundary for WebChat text protocols."""

    def prompt(self, messages, system_prompt: str, tools, client_policy):
        return _prompt(messages, system_prompt, tools, client_policy)

    def attachments(self, messages):
        return _image_attachments(messages)

    async def resolve(self, provider, answer: str, messages, tools, conversation_id, prompt):
        return await _resolve_web_answer(provider, answer, messages, tools, conversation_id, prompt)
