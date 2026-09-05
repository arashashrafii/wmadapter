from __future__ import annotations

from pathlib import Path
import asyncio
import os
import fcntl
from collections.abc import Awaitable, Callable

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


class BrowserManager:
    def __init__(
        self,
        profile_path: str = ".webbridge-profile",
        headless: bool = False,
        executable_path: str | None = None,
        cdp_endpoint: str | None = None,
    ):
        self.profile_path = Path(profile_path).expanduser().resolve()
        login_mode = os.getenv("WEBBRIDGE_LOGIN") == "1"
        self.headless = False if login_mode or os.getenv("WEBBRIDGE_XVFB") == "1" else headless
        self.executable_path = executable_path
        self.cdp_endpoint = cdp_endpoint
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._live = False
        self._stale_context: BrowserContext | None = None
        self._stale_browser: Browser | None = None
        self._stale_playwright: Playwright | None = None
        self._lock_fd: int | None = None

    def _acquire_profile_lock(self) -> None:
        if self.cdp_endpoint or self._lock_fd is not None:
            return
        lock_path = self.profile_path / ".webbridge-profile.lock"
        self.profile_path.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(f"Browser profile is locked: {self.profile_path}") from exc
        self._lock_fd = fd

    def _release_profile_lock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    @property
    def is_running(self) -> bool:
        return self.context is not None and self._live

    def _mark_disconnected(self, *_args) -> None:
        if self.context is not None:
            self._stale_context = self.context
        if self.browser is not None:
            self._stale_browser = self.browser
        if self.playwright is not None:
            self._stale_playwright = self.playwright
        self.context = None
        self.browser = None
        self.playwright = self._stale_playwright
        self._live = False

    def _register_liveness(self, context: BrowserContext) -> None:
        self._live = True
        context.on("close", self._mark_disconnected)
        browser = getattr(context, "browser", None)
        if browser is not None:
            browser.on("disconnected", self._mark_disconnected)

    async def _discard_stale(self) -> None:
        context = self._stale_context
        playwright = self._stale_playwright or self.playwright
        self._stale_context = None
        self._stale_browser = None
        self._stale_playwright = None
        self.context = None
        self.browser = None
        self.playwright = None
        self._live = False
        if context is not None and not self.cdp_endpoint:
            try:
                await context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def check_liveness(self) -> bool:
        if self.context is None or not self._live:
            return False
        try:
            _ = self.context.pages
        except Exception:
            self._mark_disconnected()
            await self._discard_stale()
            return False
        return True

    async def start(self) -> BrowserContext:
        if await self.check_liveness():
            return self.context
        if self.context is not None or self._stale_playwright is not None:
            await self._discard_stale()
        self._acquire_profile_lock()
        try:
            self.playwright = await async_playwright().start()
            if self.cdp_endpoint:
                self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_endpoint)
                if not self.browser.contexts:
                    raise RuntimeError("The Chromium CDP endpoint has no browser context")
                self.context = self.browser.contexts[0]
                self._register_liveness(self.context)
                return self.context
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_path),
                headless=self.headless,
                executable_path=self.executable_path,
                viewport={"width": 1440, "height": 1000},
            )
            self._register_liveness(self.context)
        except Exception:
            await self.stop()
            raise
        return self.context

    async def restart(self) -> BrowserContext:
        await self.stop()
        return await self.start()

    async def handoff_to_headless(
        self,
        auth_probe: Callable[[Page], Awaitable[bool]] | None = None,
    ) -> BrowserContext:
        """Close headed managed login and reopen the same profile headlessly."""
        if self.cdp_endpoint:
            raise RuntimeError("CDP mode does not support managed browser handoff")
        if self.headless:
            raise RuntimeError("Browser handoff requires a headed managed context")
        if not await self.check_liveness():
            raise RuntimeError("Headed browser is no longer running")
        try:
            await self.stop()
        except BaseException as exc:
            raise RuntimeError("headed browser shutdown failed before handoff") from exc
        self.headless = True
        try:
            context = await self.start()
            if auth_probe is not None:
                page = await self.page()
                if not await auth_probe(page):
                    raise RuntimeError("headless authentication probe returned false")
            return context
        except BaseException as exc:
            await self.stop()
            self.headless = False
            try:
                await self.start()
            except BaseException as restore_exc:
                raise RuntimeError(
                    "headless handoff failed and headed session could not be restored"
                ) from restore_exc
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(
                "headless handoff failed; headed session was restored"
            ) from exc

    async def page(self) -> Page:
        context = await self.start()
        for page in context.pages:
            if not page.is_closed():
                return page
        return await context.new_page()

    async def stop(self) -> None:
        context = self.context
        playwright = self.playwright
        self.context = None
        self.browser = None
        self.playwright = None
        self._live = False
        self._stale_context = None
        self._stale_browser = None
        self._stale_playwright = None
        if context is not None:
            try:
                if not self.cdp_endpoint:
                    await context.close()
            finally:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            finally:
                pass
        self._release_profile_lock()
