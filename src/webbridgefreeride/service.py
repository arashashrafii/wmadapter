from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from collections.abc import AsyncIterator

from .browser.manager import BrowserManager
from .providers.base import ChatProvider
from .providers.contract import ModelCapabilities
from .providers.deepseek.chat import DeepSeekChat
from .providers.deepseek.login import DeepSeekLogin
from .providers.qwen.chat import QwenChat
from .providers.deepseek.protocol import DeepSeekTextAdapter
from .providers.qwen.protocol import QwenTextAdapter
from .providers.submit import PreSubmitError, UncertainSubmitError

logger = logging.getLogger(__name__)


class PageCapacityError(RuntimeError):
    """No idle Gateway-owned page is available within the configured cap."""


@dataclass
class _PageRecord:
    page: object
    last_used: float
    active: int = 0


class DeepSeekService(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat", "deepseek-reasoner")
    capabilities = ModelCapabilities(image_input=True)
    protocol = DeepSeekTextAdapter()

    def __init__(self, config: dict):
        browser_cfg = config["browser"]
        deepseek_cfg = config["deepseek"]
        self.browser = BrowserManager(
            profile_path=browser_cfg.get("profile_dir", ".webbridge-profile"),
            headless=browser_cfg.get("headless", False),
            executable_path=browser_cfg.get("executable_path"),
            cdp_endpoint=browser_cfg.get("cdp_endpoint"),
        )
        self.chat_url = deepseek_cfg.get("chat_url", "https://chat.deepseek.com/")
        self.timeout_ms = int(deepseek_cfg.get("timeout_ms", 180000))
        self.login_timeout_ms = int(deepseek_cfg.get("login_timeout_ms", 30000))
        self.restart_retries = int(browser_cfg.get("restart_retries", 1))
        self.last_error: str | None = None
        self.ready = False
        self._conversation_pages: dict[str, object] = {}
        self._conversation_bindings: dict[str, str] = {}
        self.max_pages = browser_cfg.get("max_pages", 8)
        self.idle_timeout_ms = browser_cfg.get("idle_timeout_ms", 300000)
        self._page_records: dict[int, _PageRecord] = {}
        self._active_pages: set[int] = set()
        self._request_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._authenticate()

    async def stop(self) -> None:
        await self.browser.stop()
        self.ready = False
        self._conversation_pages.clear()
        self._conversation_bindings.clear()
        self._page_records.clear()
        self._active_pages.clear()

    async def status(self) -> dict:
        return {
            "browser_running": self.browser.is_running,
            "provider": self.name,
            "ready": self.ready,
            "last_error": self.last_error,
            "conversations": len(self._conversation_pages),
        }

    def _track_page(self, page: object) -> None:
        self._page_records.setdefault(id(page), _PageRecord(page, time.monotonic()))
        self._page_records[id(page)].last_used = time.monotonic()

    def _remove_page(self, page: object) -> None:
        for alias, candidate in list(self._conversation_pages.items()):
            if candidate is page:
                self._conversation_pages.pop(alias, None)
        self._page_records.pop(id(page), None)
        self._active_pages.discard(id(page))
        self.browser.release_page(page)

    async def cleanup_pages(self) -> int:
        if self.idle_timeout_ms is None:
            return 0
        cutoff = time.monotonic() - int(self.idle_timeout_ms) / 1000
        removed = 0
        for record in list(self._page_records.values()):
            if record.active or record.last_used > cutoff:
                continue
            page = record.page
            self._remove_page(page)
            if not page.is_closed():
                await page.close()
            removed += 1
        return removed

    def _mark_page_active(self, page: object) -> None:
        record = self._page_records.get(id(page))
        if record:
            record.active += 1
            self._active_pages.add(id(page))
            record.last_used = time.monotonic()

    def _mark_page_inactive(self, page: object) -> None:
        record = self._page_records.get(id(page))
        if record:
            record.active = max(0, record.active - 1)
            if not record.active:
                self._active_pages.discard(id(page))

    async def delete_conversation(self, conversation_id: str) -> bool:
        page = self._conversation_pages.pop(conversation_id, None)
        if page is None:
            return False
        for alias, candidate in list(self._conversation_pages.items()):
            if candidate is page:
                self._conversation_pages.pop(alias, None)
        self._remove_page(page)
        try:
            deleted = await DeepSeekChat(page, timeout_ms=self.timeout_ms).delete_remote_conversation()
        except Exception as exc:
            logger.warning("DeepSeek remote conversation deletion failed: %s", exc)
            deleted = False
        finally:
            self.browser.release_page(page)
            if not page.is_closed():
                await page.close()
        return deleted

    def conversation_url(self, conversation_id: str) -> str | None:
        """Return the provider page URL before the page is released."""
        page = self._conversation_pages.get(conversation_id)
        if page is None or page.is_closed():
            return None
        url = page.url
        return url if url.startswith("https://chat.deepseek.com/a/chat/s/") else None

    def bind_conversation(self, session_id: str, session_key: str | None = None) -> None:
        """Remember OpenClaw identifiers until the first provider request arrives."""
        if session_id and session_key:
            self._conversation_bindings[session_id] = session_key

    async def _page_for_conversation(self, conversation_id: str | None):
        if not conversation_id:
            await self.cleanup_pages()
            page = await self.browser.page_for(self.name)
            if id(page) not in self._page_records and self.max_pages is not None and len(self._page_records) >= self.max_pages:
                self.browser.release_page(page)
                if not page.is_closed():
                    await page.close()
                raise PageCapacityError(
                    f"DeepSeek page capacity reached ({self.max_pages}); close an idle conversation before opening another"
                )
            self._track_page(page)
            return page
        page = self._conversation_pages.get(conversation_id)
        if page is not None and not page.is_closed():
            self._track_page(page)
            return page
        if page is not None:
            self._remove_page(page)
        if conversation_id.startswith("auto:") and self._conversation_bindings:
            session_id, session_key = next(iter(self._conversation_bindings.items()))
        else:
            session_id = session_key = None
        await self.cleanup_pages()
        if self.max_pages is not None and len(self._page_records) >= self.max_pages:
            raise PageCapacityError(
                f"DeepSeek page capacity reached ({self.max_pages}); close an idle conversation before opening another"
            )
        page = await self.browser.page_for(self.name, conversation_id)
        self._track_page(page)
        self._conversation_pages[conversation_id] = page
        if session_id:
            self._conversation_pages[session_id] = page
        if session_key:
            self._conversation_pages[session_key] = page
            self._conversation_bindings.pop(session_id, None)
        return page

    async def _authenticate(self, conversation_id: str | None = None) -> None:
        page = await self._page_for_conversation(conversation_id)
        login = DeepSeekLogin(page, self.chat_url, timeout_ms=self.login_timeout_ms)
        await login.ensure_authenticated()
        self.ready = True
        self.last_error = None

    async def complete(self, prompt: str, conversation_id: str | None = None) -> str:
        async with self._request_lock:
            attempts = self.restart_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    page = await self._page_for_conversation(conversation_id)
                    self._mark_page_active(page)
                    try:
                        await self._authenticate(conversation_id)
                        page = await self._page_for_conversation(conversation_id)
                        chat = DeepSeekChat(page, timeout_ms=self.timeout_ms)
                        answer = await chat.send_message(prompt)
                    finally:
                        self._mark_page_inactive(page)
                    self.last_error = None
                    return answer
                except UncertainSubmitError as exc:
                    self.ready = False
                    self.last_error = str(exc)
                    logger.warning("DeepSeek submission is uncertain: %s", exc)
                    raise
                except Exception as exc:
                    self.ready = False
                    self.last_error = str(exc)
                    logger.warning("DeepSeek request failed on attempt %s/%s: %s", attempt, attempts, exc)
                    if attempt >= attempts:
                        raise
                    self._conversation_pages.clear()
                    await self.browser.restart()
            raise RuntimeError("DeepSeek request failed")

    async def complete_with_attachments(
        self, prompt: str, conversation_id: str | None = None, attachments: list[str] | None = None
    ) -> str:
        async with self._request_lock:
            attempts = self.restart_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    page = await self._page_for_conversation(conversation_id)
                    self._mark_page_active(page)
                    try:
                        await self._authenticate(conversation_id)
                        page = await self._page_for_conversation(conversation_id)
                        chat = DeepSeekChat(page, timeout_ms=self.timeout_ms)
                        answer = await chat.send_message(prompt, attachments=attachments or [])
                    finally:
                        self._mark_page_inactive(page)
                    self.last_error = None
                    return answer
                except UncertainSubmitError as exc:
                    self.ready = False
                    self.last_error = str(exc)
                    logger.warning("DeepSeek attachment submission is uncertain: %s", exc)
                    raise
                except Exception as exc:
                    self.ready = False
                    self.last_error = str(exc)
                    logger.warning("DeepSeek request with attachments failed on attempt %s/%s: %s", attempt, attempts, exc)
                    if attempt >= attempts:
                        raise
                    self._conversation_pages.clear()
                    await self.browser.restart()
            raise RuntimeError("DeepSeek request with attachments failed")

    async def stream_complete(
        self, prompt: str, conversation_id: str | None = None
    ) -> AsyncIterator[str]:
        yield await self.complete(prompt, conversation_id=conversation_id)

    async def stream_complete_with_attachments(
        self, prompt: str, conversation_id: str | None = None, attachments: list[str] | None = None
    ) -> AsyncIterator[str]:
        yield await self.complete_with_attachments(prompt, conversation_id=conversation_id, attachments=attachments)


class QwenService(ChatProvider):
    name = "qwen"
    model_ids = ("qwen-chat",)
    capabilities = ModelCapabilities(image_input=False)
    protocol = QwenTextAdapter()

    def __init__(self, config: dict):
        browser_cfg = config["browser"]
        qwen_cfg = config.get("qwen", {})
        self.browser = BrowserManager(
            profile_path=qwen_cfg.get("profile_dir", ".webbridge-profile/qwen"),
            headless=qwen_cfg.get("headless", False),
            executable_path=browser_cfg.get("executable_path"),
            cdp_endpoint=browser_cfg.get("cdp_endpoint"),
        )
        self.chat_url = qwen_cfg.get("chat_url", "https://chat.qwen.ai/")
        self.timeout_ms = int(qwen_cfg.get("timeout_ms", 180000))
        self.restart_retries = int(browser_cfg.get("restart_retries", 1))
        self.last_error: str | None = None
        self.ready = False
        self._conversation_pages: dict[str, object] = {}
        self.max_pages = browser_cfg.get("max_pages", 8)
        self.idle_timeout_ms = browser_cfg.get("idle_timeout_ms", 300000)
        self._page_records: dict[int, _PageRecord] = {}
        self._active_pages: set[int] = set()
        self._request_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._authenticate()

    async def stop(self) -> None:
        await self.browser.stop()
        self.ready = False
        self._conversation_pages.clear()
        self._page_records.clear()
        self._active_pages.clear()

    async def status(self) -> dict:
        return {
            "browser_running": self.browser.is_running,
            "provider": self.name,
            "ready": self.ready,
            "last_error": self.last_error,
            "conversations": len(self._conversation_pages),
        }

    def _track_page(self, page: object) -> None:
        self._page_records.setdefault(id(page), _PageRecord(page, time.monotonic()))
        self._page_records[id(page)].last_used = time.monotonic()

    def _remove_page(self, page: object) -> None:
        for alias, candidate in list(self._conversation_pages.items()):
            if candidate is page:
                self._conversation_pages.pop(alias, None)
        self._page_records.pop(id(page), None)
        self._active_pages.discard(id(page))
        self.browser.release_page(page)

    async def cleanup_pages(self) -> int:
        if self.idle_timeout_ms is None:
            return 0
        cutoff = time.monotonic() - int(self.idle_timeout_ms) / 1000
        removed = 0
        for record in list(self._page_records.values()):
            if record.active or record.last_used > cutoff:
                continue
            page = record.page
            self._remove_page(page)
            if not page.is_closed():
                await page.close()
            removed += 1
        return removed

    def _mark_page_active(self, page: object) -> None:
        record = self._page_records.get(id(page))
        if record:
            record.active += 1
            self._active_pages.add(id(page))
            record.last_used = time.monotonic()

    def _mark_page_inactive(self, page: object) -> None:
        record = self._page_records.get(id(page))
        if record:
            record.active = max(0, record.active - 1)
            if not record.active:
                self._active_pages.discard(id(page))

    async def delete_conversation(self, conversation_id: str) -> bool:
        page = self._conversation_pages.pop(conversation_id, None)
        if page is None:
            return False
        if not page.is_closed():
            await page.close()
        self._remove_page(page)
        return True

    async def _page_for_conversation(self, conversation_id: str | None):
        if not conversation_id:
            await self.cleanup_pages()
            page = await self.browser.page_for(self.name)
            if id(page) not in self._page_records and self.max_pages is not None and len(self._page_records) >= self.max_pages:
                self.browser.release_page(page)
                if not page.is_closed():
                    await page.close()
                raise PageCapacityError(
                    f"Qwen page capacity reached ({self.max_pages}); close an idle conversation before opening another"
                )
            self._track_page(page)
            return page
        page = self._conversation_pages.get(conversation_id)
        if page is not None and not page.is_closed():
            self._track_page(page)
            return page
        if page is not None:
            self._remove_page(page)
        await self.cleanup_pages()
        if self.max_pages is not None and len(self._page_records) >= self.max_pages:
            raise PageCapacityError(
                f"Qwen page capacity reached ({self.max_pages}); close an idle conversation before opening another"
            )
        page = await self.browser.page_for(self.name, conversation_id)
        self._track_page(page)
        self._conversation_pages[conversation_id] = page
        return page

    async def _authenticate(self, conversation_id: str | None = None) -> None:
        page = await self._page_for_conversation(conversation_id)
        chat = QwenChat(page, timeout_ms=self.timeout_ms)
        if await chat.is_authenticated():
            self.ready = True
            self.last_error = None
            return
        await page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        chat = QwenChat(page, timeout_ms=self.timeout_ms)
        if not await chat.is_authenticated():
            raise RuntimeError("Qwen is not logged in. Run `.venv/bin/python -m webbridgefreeride auth qwen` and log in manually.")
        self.ready = True
        self.last_error = None

    async def complete(self, prompt: str, conversation_id: str | None = None) -> str:
        async with self._request_lock:
            attempts = self.restart_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    page = await self._page_for_conversation(conversation_id)
                    self._mark_page_active(page)
                    try:
                        await self._authenticate(conversation_id)
                        page = await self._page_for_conversation(conversation_id)
                        answer = await QwenChat(page, timeout_ms=self.timeout_ms).send_message(prompt)
                    finally:
                        self._mark_page_inactive(page)
                    self.last_error = None
                    return answer
                except UncertainSubmitError as exc:
                    self.ready = False
                    self.last_error = str(exc)
                    logger.warning("Qwen submission is uncertain: %s", exc)
                    raise
                except Exception as exc:
                    self.ready = False
                    self.last_error = str(exc)
                    logger.warning("Qwen request failed on attempt %s/%s: %s", attempt, attempts, exc)
                    if attempt >= attempts:
                        raise
                    self._conversation_pages.clear()
                    await self.browser.restart()
            raise RuntimeError("Qwen request failed")

    async def stream_complete(self, prompt: str, conversation_id: str | None = None):
        yield await self.complete(prompt, conversation_id=conversation_id)
