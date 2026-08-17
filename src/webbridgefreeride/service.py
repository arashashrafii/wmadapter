from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from .browser.manager import BrowserManager
from .providers.base import ChatProvider
from .providers.deepseek.chat import DeepSeekChat
from .providers.deepseek.login import DeepSeekLogin

logger = logging.getLogger(__name__)


class DeepSeekService(ChatProvider):
    name = "deepseek"

    def __init__(self, config: dict):
        browser_cfg = config["browser"]
        deepseek_cfg = config["deepseek"]
        self.browser = BrowserManager(
            profile_path=browser_cfg.get("profile_dir", ".webbridge-profile"),
            headless=browser_cfg.get("headless", False),
            executable_path=browser_cfg.get("executable_path"),
        )
        self.chat_url = deepseek_cfg.get("chat_url", "https://chat.deepseek.com/")
        self.timeout_ms = int(deepseek_cfg.get("timeout_ms", 180000))
        self.login_timeout_ms = int(deepseek_cfg.get("login_timeout_ms", 30000))
        self.restart_retries = int(browser_cfg.get("restart_retries", 1))
        self.last_error: str | None = None
        self.ready = False
        self._conversation_pages: dict[str, object] = {}

    async def start(self) -> None:
        await self._authenticate()

    async def stop(self) -> None:
        await self.browser.stop()
        self.ready = False
        self._conversation_pages.clear()

    async def status(self) -> dict:
        return {
            "browser_running": self.browser.is_running,
            "provider": self.name,
            "ready": self.ready,
            "last_error": self.last_error,
            "conversations": len(self._conversation_pages),
        }

    async def _page_for_conversation(self, conversation_id: str | None):
        if not conversation_id:
            return await self.browser.page()
        page = self._conversation_pages.get(conversation_id)
        if page is not None and not page.is_closed():
            return page
        context = await self.browser.start()
        page = await context.new_page()
        self._conversation_pages[conversation_id] = page
        return page

    async def _authenticate(self, conversation_id: str | None = None) -> None:
        page = await self._page_for_conversation(conversation_id)
        login = DeepSeekLogin(page, self.chat_url, timeout_ms=self.login_timeout_ms)
        await login.ensure_authenticated()
        self.ready = True
        self.last_error = None

    async def complete(self, prompt: str, conversation_id: str | None = None) -> str:
        attempts = self.restart_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                await self._authenticate(conversation_id)
                page = await self._page_for_conversation(conversation_id)
                chat = DeepSeekChat(page, timeout_ms=self.timeout_ms)
                answer = await chat.send_message(prompt)
                self.last_error = None
                return answer
            except Exception as exc:
                self.ready = False
                self.last_error = str(exc)
                logger.warning("DeepSeek request failed on attempt %s/%s: %s", attempt, attempts, exc)
                if attempt >= attempts:
                    raise
                self._conversation_pages.clear()
                await self.browser.restart()
        raise RuntimeError("DeepSeek request failed")

    async def stream_complete(
        self, prompt: str, conversation_id: str | None = None
    ) -> AsyncIterator[str]:
        yield await self.complete(prompt, conversation_id=conversation_id)
