from __future__ import annotations

from pathlib import Path
import asyncio
import os
import json
import time
import uuid
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None
    import msvcrt
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from ..config import canonical_path


class LifecycleState:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    RESTARTING = "RESTARTING"


class BrowserManager:
    _PROVIDER_HOSTS = {
        "deepseek": {"chat.deepseek.com"},
        "qwen": {"chat.qwen.ai"},
    }

    def __init__(
        self,
        profile_path: str = ".mimicgate-profile",
        headless: bool = False,
        executable_path: str | None = None,
        cdp_endpoint: str | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        launch_url: str | None = None,
    ):
        self.profile_path = Path(canonical_path(profile_path))
        login_mode = os.getenv("MIMICGATE_LOGIN") == "1"
        xvfb = os.getenv("MIMICGATE_XVFB") == "1"
        self.headless = False if login_mode or xvfb else headless
        self.executable_path = canonical_path(executable_path) if executable_path else None
        self.cdp_endpoint = cdp_endpoint
        self.on_disconnect = on_disconnect
        self.launch_url = launch_url
        self.launch_info: dict[str, object] = {}
        self.page_event_count = 0
        self._launch_page_ids: set[int] = set()
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._live = False
        self._stale_context: BrowserContext | None = None
        self._stale_browser: Browser | None = None
        self._stale_playwright: Playwright | None = None
        self._lock_fd: int | None = None
        self._lock_metadata_path = self.profile_path / ".mimicgate-profile.lock.json"
        self._lock_token: str | None = None
        self._page_owners: dict[int, str] = {}
        self._page_claims: dict[tuple[str, str | None], Page] = {}
        self._owned_pages: dict[int, Page] = {}
        self._primary_pages: dict[str, Page] = {}
        self._lifecycle_lock = asyncio.Lock()
        self.lifecycle_state = LifecycleState.STOPPED

    async def _await_cleanup(self, awaitable) -> None:
        task = asyncio.ensure_future(awaitable)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                raise

    def _acquire_profile_lock(self) -> None:
        if self.cdp_endpoint or self._lock_fd is not None:
            return
        lock_path = self.profile_path / ".mimicgate-profile.lock"
        self.profile_path.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            details = ""
            try:
                metadata = json.loads(self._lock_metadata_path.read_text())
                details = f" (owner pid={metadata.get('owner_pid')}, profile={metadata.get('profile')})"
            except (OSError, ValueError, TypeError):
                pass
            os.close(fd)
            raise RuntimeError(f"Browser profile is locked: {self.profile_path}{details}") from exc
        self._lock_fd = fd
        self._lock_token = f"{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex}"
        metadata = {
            "owner_pid": os.getpid(),
            "owner_process_group": os.getpgid(0),
            "executable": self.executable_path,
            "profile": str(self.profile_path),
            "started_at": time.time(),
            "mode": "headless" if self.headless else "headed",
            "lock_token": self._lock_token,
        }
        temporary = self.profile_path / f".mimicgate-profile.lock.{self._lock_token}.tmp"
        try:
            temporary.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self._lock_metadata_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _release_profile_lock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            else:
                msvcrt.locking(self._lock_fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None
            if self._lock_token is not None:
                try:
                    metadata = json.loads(self._lock_metadata_path.read_text())
                    if metadata.get("lock_token") == self._lock_token:
                        self._lock_metadata_path.unlink(missing_ok=True)
                except (OSError, ValueError, TypeError):
                    pass
            self._lock_token = None

    @property
    def is_running(self) -> bool:
        return self.context is not None and self._live

    def _mark_disconnected(self, *_args) -> None:
        if self.on_disconnect is not None:
            self.on_disconnect("browser_disconnected")
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
        context.on("page", lambda *_args: setattr(self, "page_event_count", self.page_event_count + 1))
        context.on("close", self._mark_disconnected)
        browser = getattr(context, "browser", None)
        if browser is not None:
            browser.on("disconnected", self._mark_disconnected)

    @classmethod
    def _provider_page_allowed(cls, owner: str, page: Page) -> bool:
        hosts = cls._PROVIDER_HOSTS.get(owner)
        if not hosts:
            raise ValueError(f"Unknown browser page owner: {owner}")
        try:
            return urlsplit(page.url).hostname in hosts
        except Exception:
            return False

    def _release_page(self, page: Page) -> None:
        page_id = id(page)
        self._page_owners.pop(page_id, None)
        self._owned_pages.pop(page_id, None)
        for key, claimed in list(self._page_claims.items()):
            if claimed is page:
                self._page_claims.pop(key, None)
        for owner, primary in list(self._primary_pages.items()):
            if primary is page:
                self._primary_pages.pop(owner, None)

    def _claim_page(self, owner: str, page: Page, conversation_id: str | None) -> Page:
        page_id = id(page)
        existing_owner = self._page_owners.get(page_id)
        if existing_owner is not None and existing_owner != owner:
            raise RuntimeError("browser page is owned by another provider")
        self._page_owners[page_id] = owner
        self._owned_pages[page_id] = page
        self._page_claims[(owner, conversation_id)] = page
        page.on("close", lambda *_args: self._release_page(page))
        return page

    async def page_for(self, owner: str, conversation_id: str | None = None) -> Page:
        """Return a page claimed by owner and matching its provider origin."""
        await self.start()
        key = (owner, conversation_id)
        claimed = self._page_claims.get(key)
        if claimed is not None and not claimed.is_closed():
            return claimed
        self._page_claims.pop(key, None)
        for page in self.context.pages:
            if page.is_closed() or not self._provider_page_allowed(owner, page):
                continue
            page_id = id(page)
            page_owner = self._page_owners.get(page_id)
            if page_owner is not None and page_owner != owner:
                continue
            if conversation_id is not None and any(
                claimed_page is page and claim_owner == owner and claim_id != conversation_id
                for (claim_owner, claim_id), claimed_page in self._page_claims.items()
            ):
                continue
            return self._claim_page(owner, page, conversation_id)
        page = await self.context.new_page()
        return self._claim_page(owner, page, conversation_id)

    async def primary_page(self, owner: str) -> Page:
        """Return one provider login page and close only extra managed pages."""
        await self.start()
        primary = self._primary_pages.get(owner)
        if primary is not None and not primary.is_closed():
            return primary
        candidates = [page for page in self.context.pages if not page.is_closed() and self._provider_page_allowed(owner, page)]
        if candidates:
            primary = candidates[0]
        else:
            # Persistent headed contexts normally start with one about:blank page;
            # reuse it so login never creates a duplicate tab before navigation.
            existing = [page for page in self.context.pages if not page.is_closed()]
            if not existing:
                raise RuntimeError("managed login requires the browser launch page")
            primary = existing[0]
        self._primary_pages[owner] = primary
        for extra in candidates[1:]:
            await extra.close()
        for extra in list(self.context.pages):
            if extra is primary or extra.is_closed() or id(extra) not in self._launch_page_ids:
                continue
            if getattr(extra, "url", "") in {"", "about:blank"}:
                await extra.close()
        return primary

    def release_page(self, page: Page) -> None:
        self._release_page(page)

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
        async with self._lifecycle_lock:
            return await self._start_unlocked()

    async def _start_unlocked(self) -> BrowserContext:
        if await self.check_liveness():
            return self.context
        if self.context is not None or self._stale_playwright is not None:
            await self._discard_stale()
        self.lifecycle_state = LifecycleState.STARTING
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
            launch_kwargs = {
                "user_data_dir": str(self.profile_path),
                "headless": self.headless,
                "executable_path": self.executable_path,
                "viewport": {"width": 1440, "height": 1000},
            }
            if not self.headless:
                # Keep interactive login app-like while leaving headless API and CDP untouched.
                launch_kwargs["args"] = [
                    f"--app={self.launch_url or 'about:blank'}",
                    "--disable-sync",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--no-first-run",
                ]
            self.launch_info = {
                "argv": list(launch_kwargs.get("args", [])),
                "executable": self.executable_path,
                "profile": str(self.profile_path),
                "headless": self.headless,
                "revision": getattr(getattr(self.playwright, "chromium", None), "_revision", None),
                "pid": None,
            }
            self.context = await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
            self._launch_page_ids = {id(page) for page in self.context.pages}
            browser_process = getattr(getattr(self.context, "browser", None), "_impl_obj", None)
            process = getattr(browser_process, "_process", None) or getattr(browser_process, "process", None)
            self.launch_info["pid"] = getattr(process, "pid", None)
            self._register_liveness(self.context)
            self.lifecycle_state = LifecycleState.RUNNING
        except Exception:
            await self._stop_unlocked()
            raise
        except BaseException:
            await self._stop_unlocked()
            raise
        return self.context

    async def restart(self) -> BrowserContext:
        async with self._lifecycle_lock:
            self.lifecycle_state = LifecycleState.RESTARTING
            await self._stop_unlocked()
            return await self._start_unlocked()

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
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        self.lifecycle_state = LifecycleState.STOPPING
        context = self.context
        playwright = self.playwright
        self.context = None
        self.browser = None
        self.playwright = None
        self._live = False
        self._stale_context = None
        self._stale_browser = None
        self._stale_playwright = None
        self._page_owners.clear()
        self._page_claims.clear()
        self._owned_pages.clear()
        self._primary_pages.clear()
        failure = None
        if context is not None:
            try:
                if not self.cdp_endpoint:
                    await self._await_cleanup(context.close())
            except BaseException as exc:
                failure = exc
        if playwright is not None:
            try:
                await self._await_cleanup(playwright.stop())
            except BaseException as exc:
                failure = failure or exc
        self._release_profile_lock()
        self.lifecycle_state = LifecycleState.STOPPED
        if failure is not None:
            raise failure
