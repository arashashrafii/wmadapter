"""Browser lifecycle manager for web based providers.

Milestone 1 implementation uses a persistent Chromium profile so the user
can authenticate once and reuse the session.
"""

from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext


class BrowserManager:
    def __init__(self, profile_path: str = "data/browser"):
        self.profile_path = Path(profile_path)
        self.playwright = None
        self.context: BrowserContext | None = None

    async def start(self):
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.profile_path),
            headless=False,
        )
        return self.context

    async def stop(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
