from __future__ import annotations

import asyncio
import inspect

from playwright.async_api import Page
from ...browser.elements import first_visible

from .selectors import CHAT_INPUTS, RESPONSE_BLOCKS
from ..deepseek.login import CHAT_READY, CHALLENGE_VISIBLE, SIGN_IN_VISIBLE, SESSION_PENDING, UNKNOWN_UI
from ..submit import PreSubmitError, SubmitState, UncertainSubmitError


class QwenChat:
    def __init__(self, page: Page, timeout_ms: int = 180000):
        self.page = page
        self.timeout_ms = timeout_ms
        self.submit_state = SubmitState.NOT_SUBMITTED
        self._ready_probe_streak = 0

    async def _first_visible(self, selectors: list[str]):
        return await first_visible(self.page, selectors, "Qwen")

    async def _locator(self, root, selector: str):
        locator = root.locator(selector)
        return await locator if inspect.isawaitable(locator) else locator

    async def probe_auth(self) -> str:
        """Read current DOM state without navigation or submission."""
        for selector in ("iframe[src*='captcha']", "[id*='captcha' i]", "[class*='challenge' i]"):
            try:
                locator = await self._locator(self.page, selector)
                if await locator.last.is_visible(timeout=300):
                    self._ready_probe_streak = 0
                    return CHALLENGE_VISIBLE
            except Exception:
                pass
        try:
            field = await self._first_visible(CHAT_INPUTS)
            if await field.is_editable(timeout=300):
                self._ready_probe_streak += 1
                if self._ready_probe_streak >= 2:
                    return CHAT_READY
                return SESSION_PENDING
        except Exception:
            pass
        self._ready_probe_streak = 0
        locator = await self._locator(self.page, "text=/sign in|log in|login/i")
        count = locator.count()
        count = await count if inspect.isawaitable(count) else count
        return SIGN_IN_VISIBLE if count else UNKNOWN_UI

    async def is_authenticated(self) -> bool:
        return await self.probe_auth() == CHAT_READY

    async def _response_counts(self) -> dict[str, int]:
        return {selector: await self.page.locator(selector).count() for selector in RESPONSE_BLOCKS}

    async def _latest_response_text(self, previous_counts: dict[str, int]) -> str:
        for selector in RESPONSE_BLOCKS:
            blocks = self.page.locator(selector)
            count = await blocks.count()
            previous_count = previous_counts.get(selector, 0)
            if count > previous_count:
                text = (await blocks.last.inner_text()).strip()
                if text:
                    return self._clean_response(text)
        return ""

    def _clean_response(self, text: str) -> str:
        if "Thinking completed" in text:
            text = text.split("Thinking completed", 1)[-1].strip()
        if "AI-generated content may not be accurate." in text:
            text = text.split("AI-generated content may not be accurate.", 1)[0].strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        noise = {"Auto", "How can I help you ?", "Qwen3.7-Plus"}
        lines = [line for line in lines if line not in noise]
        return "\n".join(lines).strip()

    async def send_message(self, message: str) -> str:
        self.submit_state = SubmitState.NOT_SUBMITTED
        try:
            input_box = await self._first_visible(CHAT_INPUTS)
            await self.page.wait_for_timeout(1000)
            previous_counts = await self._response_counts()

            await input_box.click()
            await input_box.fill(message)
            await self.page.wait_for_timeout(300)
            self.submit_state = SubmitState.SUBMITTING
            self.submit_state = SubmitState.SUBMITTED_UNCERTAIN
            await input_box.press("Enter")

            deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
            last_text = ""
            stable_rounds = 0

            while asyncio.get_running_loop().time() < deadline:
                text = await self._latest_response_text(previous_counts)
                if text:
                    if text == last_text:
                        stable_rounds += 1
                    else:
                        stable_rounds = 0
                        last_text = text
                    if stable_rounds >= 2:
                        self.submit_state = SubmitState.COMPLETED
                        return text
                await asyncio.sleep(1)

            raise TimeoutError("Qwen response was not detected before timeout")
        except UncertainSubmitError:
            raise
        except Exception as exc:
            if self.submit_state in (SubmitState.SUBMITTING, SubmitState.SUBMITTED_UNCERTAIN):
                raise UncertainSubmitError(str(exc)) from exc
            raise PreSubmitError(str(exc)) from exc
