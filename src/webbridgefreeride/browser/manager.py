from __future__ import annotations

from pathlib import Path
import os

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright


class BrowserManager:
    def __init__(
        self,
        profile_path: str = ".webbridge-profile",
        headless: bool = False,
        executable_path: str | None = None,
    ):
        self.profile_path = Path(profile_path)
        login_mode = os.getenv("WEBBRIDGE_LOGIN") == "1"
        self.headless = False if login_mode or os.getenv("WEBBRIDGE_XVFB") == "1" else headless
        self.executable_path = executable_path
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None

    @property
    def is_running(self) -> bool:
        return self.context is not None

    async def start(self) -> BrowserContext:
        if self.context is not None:
            return self.context
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_path),
                headless=self.headless,
                executable_path=self.executable_path,
                viewport={"width": 1440, "height": 1000},
            )
        except Exception:
            await self.stop()
            raise
        return self.context

    async def restart(self) -> BrowserContext:
        await self.stop()
        return await self.start()

    async def page(self) -> Page:
        context = await self.start()
        for page in context.pages:
            if not page.is_closed():
                return page
        return await context.new_page()

    async def stop(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            finally:
                self.context = None
        if self.playwright is not None:
            try:
                await self.playwright.stop()
            finally:
                self.playwright = None
