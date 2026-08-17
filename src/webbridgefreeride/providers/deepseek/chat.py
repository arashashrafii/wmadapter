from __future__ import annotations

import asyncio

from playwright.async_api import Page

from .selectors import CHAT_INPUTS, RESPONSE_BLOCKS


class DeepSeekChat:
    def __init__(self, page: Page, timeout_ms: int = 180000):
        self.page = page
        self.timeout_ms = timeout_ms

    async def _first_visible(self, selectors: list[str]):
        for selector in selectors:
            locator = self.page.locator(selector).last
            try:
                if await locator.is_visible(timeout=1500):
                    return locator
            except Exception:
                continue
        raise RuntimeError(f"No visible DeepSeek element found for selectors: {selectors}")

    async def send_message(self, message: str) -> str:
        input_box = await self._first_visible(CHAT_INPUTS)
        response_locator = None
        previous_count = 0
        for selector in RESPONSE_BLOCKS:
            locator = self.page.locator(selector)
            count = await locator.count()
            if count >= previous_count:
                previous_count = count
                response_locator = locator

        await input_box.fill(message)
        await input_box.press("Enter")

        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
        last_text = ""
        stable_rounds = 0

        while asyncio.get_running_loop().time() < deadline:
            for selector in RESPONSE_BLOCKS:
                blocks = self.page.locator(selector)
                if await blocks.count() > previous_count:
                    text = (await blocks.last.inner_text()).strip()
                    if text:
                        if text == last_text:
                            stable_rounds += 1
                        else:
                            stable_rounds = 0
                            last_text = text
                        if stable_rounds >= 3:
                            return text
            await asyncio.sleep(1)

        if last_text:
            return last_text
        raise TimeoutError("DeepSeek response was not detected before timeout")
