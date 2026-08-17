from __future__ import annotations

from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright


class BrowserManager:
    def __init__(
        self,
        profile_path: str = ".webbridge-profile",
        headless: bool = False,
        executable_path: str | None = None,
    ):
        self.profile_path = Path(profile_path)
        self.headless = headless
        self.executable_path = executable_path
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None

    async def start(self) -> BrowserContext:
        if self.context is not None:
            return self.context
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_path),
            headless=self.headless,
            executable_path=self.executable_path,
            viewport={"width": 1440, "height": 1000},
        )
        return self.context

    async def page(self) -> Page:
        context = await self.start()
        if context.pages:
            return context.pages[0]
        return await context.new_page()

    async def stop(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None
