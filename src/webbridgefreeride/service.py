from __future__ import annotations

from .browser.manager import BrowserManager
from .providers.deepseek.chat import DeepSeekChat
from .providers.deepseek.login import DeepSeekLogin


class DeepSeekService:
    def __init__(self, config: dict):
        browser_cfg = config["browser"]
        deepseek_cfg = config["deepseek"]
        self.browser = BrowserManager(
            profile_path=browser_cfg.get("profile_dir", ".webbridge-profile"),
            headless=browser_cfg.get("headless", False),
        )
        self.chat_url = deepseek_cfg.get("chat_url", "https://chat.deepseek.com/")
        self.timeout_ms = int(deepseek_cfg.get("timeout_ms", 180000))

    async def start(self) -> None:
        page = await self.browser.page()
        login = DeepSeekLogin(page, self.chat_url)
        await login.ensure_authenticated()

    async def stop(self) -> None:
        await self.browser.stop()

    async def complete(self, prompt: str) -> str:
        page = await self.browser.page()
        login = DeepSeekLogin(page, self.chat_url)
        await login.ensure_authenticated()
        chat = DeepSeekChat(page, timeout_ms=self.timeout_ms)
        return await chat.send_message(prompt)
