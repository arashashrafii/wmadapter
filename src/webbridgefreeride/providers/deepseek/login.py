from __future__ import annotations

import os

from playwright.async_api import Page

from ...credentials import CredentialStore, CredentialStoreError
from .selectors import CHAT_INPUTS, COOKIE_ACCEPT, LOGIN_AGREE, LOGIN_EMAIL, LOGIN_PASSWORD, LOGIN_SUBMIT


class DeepSeekLogin:
    def __init__(self, page: Page, chat_url: str = "https://chat.deepseek.com/", timeout_ms: int = 30000):
        self.page = page
        self.chat_url = chat_url
        self.timeout_ms = timeout_ms

    async def open(self) -> None:
        response = await self.page.goto(self.chat_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"DeepSeek returned HTTP {response.status}; the website or network blocked the browser request")

    async def _click_first_visible(self, selectors: list[str], timeout: int = 1000) -> bool:
        for selector in selectors:
            locator = self.page.locator(selector).last
            try:
                if await locator.is_visible(timeout=timeout):
                    await locator.click()
                    return True
            except Exception:
                continue
        return False

    async def accept_cookies(self) -> None:
        await self._click_first_visible(COOKIE_ACCEPT, timeout=800)

    async def is_authenticated(self) -> bool:
        for selector in CHAT_INPUTS:
            try:
                if await self.page.locator(selector).last.is_visible(timeout=1200):
                    return True
            except Exception:
                pass
        return False

    def _credentials(self) -> tuple[str, str] | None:
        if os.getenv("WEBBRIDGE_LOGIN") == "1":
            return None
        email = os.getenv("DEEPSEEK_EMAIL")
        password = os.getenv("DEEPSEEK_PASSWORD")
        if email and password:
            return email, password
        try:
            return CredentialStore().load()
        except CredentialStoreError as exc:
            raise RuntimeError(str(exc)) from exc

    async def ensure_authenticated(self) -> None:
        # Keep the current DeepSeek page when it is already logged in. Navigating
        # to the home URL for every API request starts a new web conversation.
        if await self.is_authenticated():
            return

        await self.open()
        await self.accept_cookies()
        if await self.is_authenticated():
            return

        credentials = self._credentials()
        if credentials is None:
            raise RuntimeError(
                "DeepSeek is not logged in. Run `.venv/bin/python -m webbridgefreeride credentials set`, "
                "set DEEPSEEK_EMAIL and DEEPSEEK_PASSWORD, or log in manually in the opened browser."
            )
        email, password = credentials

        await self.accept_cookies()
        await self.page.locator(LOGIN_EMAIL).fill(email, timeout=self.timeout_ms)
        await self.page.locator(LOGIN_PASSWORD).fill(password, timeout=self.timeout_ms)
        try:
            checkbox = self.page.locator(LOGIN_AGREE)
            if await checkbox.is_visible(timeout=1000):
                await checkbox.click()
        except Exception:
            pass
        if not await self._click_first_visible(LOGIN_SUBMIT, timeout=1500):
            raise RuntimeError("DeepSeek login submit button was not found")
        await self.page.wait_for_timeout(3000)

        if not await self.is_authenticated():
            raise RuntimeError(
                "Automatic DeepSeek login did not complete. CAPTCHA, verification, invalid credentials, "
                "or a UI change may require manual login."
            )
