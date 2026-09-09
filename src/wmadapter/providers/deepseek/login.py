from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any

from playwright.async_api import Page

from .selectors import CHAT_INPUTS, COOKIE_ACCEPT, LOGIN_EMAIL, LOGIN_PASSWORD, LOGIN_SUBMIT

CHAT_READY = "CHAT_READY"
CHALLENGE_VISIBLE = "CHALLENGE_VISIBLE"
SIGN_IN_VISIBLE = "SIGN_IN_VISIBLE"
SESSION_PENDING = "SESSION_PENDING"
UNKNOWN_UI = "UNKNOWN_UI"
ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
RATE_LIMITED = "RATE_LIMITED"
AUTH_PROBE_STATES = (CHALLENGE_VISIBLE, SIGN_IN_VISIBLE, SESSION_PENDING, CHAT_READY, UNKNOWN_UI, ACCOUNT_SUSPENDED, RATE_LIMITED)

CHALLENGE_SELECTORS = [
    "iframe[src*='captcha' i]", "iframe[src*='challenge' i]",
    "iframe[title*='captcha' i]", "iframe[title*='challenge' i]",
    "[data-testid*='captcha' i]", "[data-testid*='challenge' i]",
    "[id^='captcha' i]", "[id^='challenge' i]",
]
SIGN_IN_SELECTORS = [
    LOGIN_EMAIL, LOGIN_PASSWORD, *LOGIN_SUBMIT,
]


class DeepSeekLogin:
    def __init__(self, page: Page, chat_url: str = "https://chat.deepseek.com/", timeout_ms: int = 30000):
        self.page = page
        self.chat_url = chat_url
        self.timeout_ms = timeout_ms
        self._ready_probe_streak = 0
        self.last_probe_diagnostic: dict[str, Any] = {
            "state": UNKNOWN_UI,
            "reason": "probe_not_run",
            "url": getattr(page, "url", None),
        }

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

    async def _visible(self, root: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = root.locator(selector)
                if inspect.isawaitable(locator):
                    locator = await locator
                locator = locator.last
                if await locator.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    async def _chat_input_diagnostics(self, roots: list[Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for root_index, root in enumerate(roots):
            for selector in CHAT_INPUTS:
                result: dict[str, Any] = {"root": root_index, "selector": selector}
                try:
                    locator = root.locator(selector)
                    count = await locator.count()
                    result["count"] = count
                    if count:
                        field = locator.last
                        result["visible"] = await field.is_visible(timeout=300)
                        result["editable"] = await field.is_editable(timeout=300)
                except Exception as exc:
                    result["error"] = type(exc).__name__
                results.append(result)
        return results

    async def probe_auth(self) -> str:
        """Inspect the current UI only; this method never navigates or submits."""
        roots = [self.page, *getattr(self.page, "frames", [])]
        try:
            body_text = await self.page.locator("body").inner_text(timeout=500)
        except Exception:
            body_text = ""
        if re.search(r"account has been suspended|violation of user policies", body_text, re.IGNORECASE):
            self._ready_probe_streak = 0
            self.last_probe_diagnostic = {"state": ACCOUNT_SUSPENDED, "reason": "account_suspended", "url": self.page.url}
            return ACCOUNT_SUSPENDED
        if re.search(r"too many requests|40029", body_text, re.IGNORECASE):
            self._ready_probe_streak = 0
            self.last_probe_diagnostic = {"state": RATE_LIMITED, "reason": "provider_rate_limited", "url": self.page.url}
            return RATE_LIMITED
        challenge = any([await self._visible(root, CHALLENGE_SELECTORS) for root in roots])
        if challenge:
            self._ready_probe_streak = 0
            self.last_probe_diagnostic = {"state": CHALLENGE_VISIBLE, "reason": "challenge_visible", "url": self.page.url}
            return CHALLENGE_VISIBLE
        sign_in = any([await self._visible(root, SIGN_IN_SELECTORS) for root in roots])
        if sign_in:
            self._ready_probe_streak = 0
            self.last_probe_diagnostic = {"state": SIGN_IN_VISIBLE, "reason": "sign_in_visible", "url": self.page.url}
            return SIGN_IN_VISIBLE
        for root in roots:
            for selector in CHAT_INPUTS:
                try:
                    field = root.locator(selector).last
                    if await field.is_visible(timeout=300) and await field.is_editable(timeout=300):
                        self._ready_probe_streak += 1
                        state = CHAT_READY if self._ready_probe_streak >= 2 else SESSION_PENDING
                        self.last_probe_diagnostic = {
                            "state": state,
                            "reason": "editable_chat_input",
                            "selector": selector,
                            "url": self.page.url,
                        }
                        if self._ready_probe_streak >= 2:
                            return CHAT_READY
                        return SESSION_PENDING
                except Exception:
                    continue
        self._ready_probe_streak = 0
        self.last_probe_diagnostic = {
            "state": UNKNOWN_UI,
            "reason": "no_visible_editable_chat_input",
            "url": self.page.url,
            "frame_count": len(roots) - 1,
            "chat_inputs": await self._chat_input_diagnostics(roots),
        }
        return UNKNOWN_UI

    async def is_authenticated(self) -> bool:
        """Compatibility wrapper; callers needing reasons must use probe_auth."""
        return await self.probe_auth() == CHAT_READY

    async def ensure_authenticated(self) -> None:
        # Keep the current DeepSeek page when it is already logged in. Navigating
        # to the home URL for every API request starts a new web conversation.
        if await self.probe_auth() == CHAT_READY:
            return
        await self.accept_cookies()
        state = await self.probe_auth()
        if state == CHAT_READY:
            return
        if state == ACCOUNT_SUSPENDED:
            raise RuntimeError("DeepSeek account is suspended by the provider; automatic retries are disabled")
        if state in (CHALLENGE_VISIBLE, UNKNOWN_UI, SESSION_PENDING):
            raise RuntimeError(f"DeepSeek authentication is pending ({state.lower()})")

        raise RuntimeError("DeepSeek is not logged in. Complete login manually in the isolated Google Chrome browser.")
