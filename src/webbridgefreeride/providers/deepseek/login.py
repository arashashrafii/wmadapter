from __future__ import annotations

import os

from playwright.async_api import Page

from .selectors import CHAT_INPUTS, LOGIN_AGREE, LOGIN_EMAIL, LOGIN_PASSWORD, LOGIN_SUBMIT


class DeepSeekLogin:
    def __init__(self, page: Page, chat_url: str = "https://chat.deepseek.com/"):
        self.page = page
        self.chat_url = chat_url

    async def open(self) -> None:
        await self.page.goto(self.chat_url, wait_until="domcontentloaded")

    async def is_authenticated(self) -> bool:
        for selector in CHAT_INPUTS:
            try:
                if await self.page.locator(selector).last.is_visible(timeout=1200):
                    return True
            except Exception:
                pass
        return False

    async def ensure_authenticated(self) -> None:
        await self.open()
        if await self.is_authenticated():
            return

        email = os.getenv("DEEPSEEK_EMAIL")
        password = os.getenv("DEEPSEEK_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                "DeepSeek is not logged in. Set DEEPSEEK_EMAIL and DEEPSEEK_PASSWORD "
                "for first login, or log in manually in the opened browser and restart."
            )

        await self.page.locator(LOGIN_EMAIL).fill(email)
        await self.page.locator(LOGIN_PASSWORD).fill(password)
        try:
            checkbox = self.page.locator(LOGIN_AGREE)
            if await checkbox.is_visible(timeout=1000):
                await checkbox.click()
        except Exception:
            pass
        await self.page.locator(LOGIN_SUBMIT).click()
        await self.page.wait_for_timeout(3000)

        if not await self.is_authenticated():
            raise RuntimeError(
                "Automatic DeepSeek login did not complete. CAPTCHA, verification, or UI change may require manual login."
            )
