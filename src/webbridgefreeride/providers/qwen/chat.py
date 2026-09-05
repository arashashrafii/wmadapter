from __future__ import annotations

import asyncio

from playwright.async_api import Page
from ...browser.elements import first_visible

from .selectors import CHAT_INPUTS, RESPONSE_BLOCKS


class QwenChat:
    def __init__(self, page: Page, timeout_ms: int = 180000):
        self.page = page
        self.timeout_ms = timeout_ms

    async def _first_visible(self, selectors: list[str]):
        return await first_visible(self.page, selectors, "Qwen")

    async def is_authenticated(self) -> bool:
        try:
            await self._first_visible(CHAT_INPUTS)
            return True
        except Exception:
            return False

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
        input_box = await self._first_visible(CHAT_INPUTS)
        await self.page.wait_for_timeout(1000)
        previous_counts = await self._response_counts()

        await input_box.click()
        await input_box.fill(message)
        await self.page.wait_for_timeout(300)
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
                    return text
            await asyncio.sleep(1)

        raise TimeoutError("Qwen response was not detected before timeout")
