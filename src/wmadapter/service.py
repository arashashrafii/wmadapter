from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from collections.abc import AsyncIterator

from .browser.manager import BrowserManager
from .providers.base import ChatProvider
from .providers.contract import ModelCapabilities
from .providers.deepseek.chat import DeepSeekChat
from .providers.deepseek.login import DeepSeekLogin
from .providers.deepseek.login import CHAT_READY, CHALLENGE_VISIBLE, SIGN_IN_VISIBLE, UNKNOWN_UI, DeepSeekLogin
from .providers.qwen.chat import QwenChat
from .providers.deepseek.protocol import DeepSeekTextAdapter
from .providers.qwen.protocol import QwenTextAdapter
from .providers.submit import PreSubmitError, UncertainSubmitError
from .config import provider_profile_dir

logger = logging.getLogger(__name__)

AUTH_STATES = (
    "STARTING", "CHECKING_SESSION", "LOGIN_REQUIRED", "AUTHENTICATING",
    "HANDOFF", "VERIFYING_SESSION", "READY", "LOGIN_INTERRUPTED", "LOGIN_CANCELLED",
)


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
        self.chat_url = deepseek_cfg.get("chat_url", "https://chat.deepseek.com/")
        self.browser = BrowserManager(
            profile_path=browser_cfg.get("profile_dir", provider_profile_dir("deepseek")),
            headless=browser_cfg.get("headless", False),
            executable_path=browser_cfg.get("executable_path"),
            cdp_endpoint=browser_cfg.get("cdp_endpoint"),
            mode=browser_cfg.get("mode"),
            on_disconnect=self._on_browser_disconnect,
            launch_url=self.chat_url,
        )
        self.timeout_ms = int(deepseek_cfg.get("timeout_ms", 180000))
        self.login_timeout_ms = int(deepseek_cfg.get("login_timeout_ms", 30000))
        self.restart_retries = int(browser_cfg.get("restart_retries", 1))
        self.last_error: str | None = None
        self.ready = False
        self.auth_state = "STARTING"
        self.reason_code = "starting"
        self.updated_at = time.time()
        self.login_attempt_id: str | None = None
        self._conversation_pages: dict[str, object] = {}
        self._conversation_bindings: dict[str, str] = {}
        self._fallback_aliases: set[str] = {"auto:openclaw"}
        self.max_pages = browser_cfg.get("max_pages", 8)
        self.idle_timeout_ms = browser_cfg.get("idle_timeout_ms", 300000)
        self._page_records: dict[int, _PageRecord] = {}
        self._active_pages: set[int] = set()
        self._request_lock = asyncio.Lock()
        self._auth_watch_task: asyncio.Task | None = None
        self._auth_generation = 0
        self._watcher_running = False
        self._last_probe_at: float | None = None
        self._last_probe_result: str | None = None
        self._initial_start_available = True

    async def start(self) -> None:
        if self.auth_state == "LOGIN_INTERRUPTED":
            raise RuntimeError("login_interrupted: use retry_login explicitly")
        if not self._initial_start_available:
            raise RuntimeError("login attempt already started; use retry_login explicitly")
        self._initial_start_available = False
        self.login_attempt_id = uuid.uuid4().hex
        self.browser.set_diagnostics(provider=self.name, login_attempt_id=self.login_attempt_id, auth_state=self.auth_state)
        self._set_auth_state("CHECKING_SESSION", "checking_session")
        self._auth_generation += 1
        try:
            await self._authenticate()
        except Exception:
            if self.auth_state not in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
                self._start_auth_watcher(self._auth_generation)
            raise

    def _start_auth_watcher(self, generation: int) -> None:
        if self.auth_state in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
            return
        if self._auth_watch_task is None or self._auth_watch_task.done():
            self._auth_watch_task = asyncio.create_task(self._watch_auth(generation))

    def _cancel_auth_watcher(self) -> None:
        task = self._auth_watch_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._watcher_running = False

    async def _watch_auth(self, generation: int) -> None:
        self._watcher_running = True
        ready_streak = 0
        probe = None
        try:
            while generation == self._auth_generation and self.auth_state not in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
                page = self.browser._primary_pages.get(self.name)
                if page is None:
                    page = next((candidate for (owner, _), candidate in self.browser._page_claims.items() if owner == self.name), None)
                if page is None or page.is_closed() or not self.browser.is_running:
                    return
                if probe is None or probe.page is not page:
                    probe = DeepSeekLogin(page, self.chat_url)
                state = await probe.probe_auth()
                self._last_probe_at = time.time()
                self._last_probe_result = state
                if state == CHAT_READY:
                    ready_streak += 1
                    if ready_streak >= 2:
                        self._set_auth_state("HANDOFF", "chat_ready")
                        if not self.browser.headless:
                            async def auth_probe(candidate) -> bool:
                                return await DeepSeekLogin(candidate, self.chat_url).probe_auth() == CHAT_READY

                            await self.browser.handoff_to_headless(auth_probe=auth_probe)
                            self._clear_conversation_pages()
                        self._set_auth_state("VERIFYING_SESSION", "session_probe")
                        verify_page = await self.browser.page()
                        if await DeepSeekLogin(verify_page, self.chat_url).probe_auth() == CHAT_READY:
                            self.ready = True
                            self._set_auth_state("READY", "authenticated")
                            return
                else:
                    ready_streak = 0
                    if state == SIGN_IN_VISIBLE:
                        self._set_auth_state("LOGIN_REQUIRED", "provider_login_required")
                    elif state == CHALLENGE_VISIBLE:
                        self._set_auth_state("LOGIN_REQUIRED", "challenge_visible")
                    elif state == UNKNOWN_UI:
                        self._set_auth_state("AUTHENTICATING", "unknown_ui")
                    else:
                        self._set_auth_state("AUTHENTICATING", "session_pending")
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("DeepSeek auth watcher failed")
        finally:
            self._watcher_running = False

    def _on_browser_disconnect(self, reason: str) -> None:
        if self.auth_state in {"STARTING", "CHECKING_SESSION", "LOGIN_REQUIRED", "AUTHENTICATING", "HANDOFF", "VERIFYING_SESSION"}:
            if not self.browser.lifecycle_events or self.browser.lifecycle_events[-1].get("reason") != reason:
                self.browser._emit_lifecycle("auth.interrupted", initiator="external", reason=reason)
            self._set_auth_state("LOGIN_INTERRUPTED", reason)
            self._cancel_auth_watcher()

    async def retry_login(self) -> None:
        """Explicitly reset a cancelled login and start exactly one attempt."""
        self.ready = False
        self.last_error = None
        self._cancel_auth_watcher()
        self._initial_start_available = True
        self._set_auth_state("STARTING", "explicit_retry")
        await self.start()

    async def stop(self) -> None:
        self._cancel_auth_watcher()
        await self.browser.stop()
        self.ready = False
        self._conversation_pages.clear()
        self._conversation_bindings.clear()
        self._fallback_aliases = {"auto:openclaw"}
        self._page_records.clear()
        self._active_pages.clear()

    async def status(self) -> dict:
        return {
            "browser_running": self.browser.is_running,
            "browser_mode": self.browser.mode,
            "browser_ownership": "user" if self.browser.mode == "cdp" else "managed",
            "provider": self.name,
            "ready": self.ready,
            "last_error": self.last_error,
            "conversations": len(self._conversation_pages),
            "unique_pages": len({id(page) for page in self._conversation_pages.values()}),
            "conversation_aliases": len(self._conversation_pages),
            "state": self.auth_state,
            "reason_code": self.reason_code,
            "updated_at": self.updated_at,
            "login_attempt_id": self.login_attempt_id,
            "watcher_running": self._watcher_running,
            "last_probe_at": self._last_probe_at,
            "last_probe_result": self._last_probe_result,
            "browser_generation": self.browser.browser_generation,
            "lifecycle_events": list(self.browser.lifecycle_events),
            "last_lifecycle_event": self.browser.lifecycle_events[-1] if self.browser.lifecycle_events else None,
        }

    def _set_auth_state(self, state: str, reason_code: str) -> None:
        self.auth_state = state
        self.reason_code = reason_code
        self.updated_at = time.time()
        self.browser.set_diagnostics(provider=self.name, login_attempt_id=self.login_attempt_id, auth_state=state)
        self.browser._emit_lifecycle(
            "auth.ready" if state == "READY" else "auth.state",
            initiator="wmadapter",
            reason=reason_code,
        )

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
            page = self._conversation_pages.get("auto:openclaw")
            if page is not None and not page.is_closed():
                self._conversation_pages[session_id] = page
                self._conversation_pages[session_key] = page
                self._fallback_aliases.update((session_id, session_key))
            else:
                self._conversation_bindings[session_id] = session_key

    async def _page_for_conversation(self, conversation_id: str | None):
        if self.auth_state in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
            raise RuntimeError("provider_login_required")
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
        if conversation_id == "auto:openclaw":
            for alias in self._fallback_aliases:
                self._conversation_pages[alias] = page
            self._conversation_bindings.clear()
        if session_id:
            self._conversation_pages[session_id] = page
        if session_key:
            self._conversation_pages[session_key] = page
            self._conversation_bindings.pop(session_id, None)
        return page

    def _clear_conversation_pages(self) -> None:
        """Drop page references invalidated by a browser lifecycle transition."""
        fallback_page = self._conversation_pages.get("auto:openclaw")
        if fallback_page is not None:
            self._fallback_aliases.update(
                alias for alias, page in self._conversation_pages.items() if page is fallback_page
            )
        self._conversation_pages.clear()
        self._page_records.clear()
        self._active_pages.clear()

    @staticmethod
    def _is_auth_failure(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in (
            "challenge_visible", "sign_in_visible", "unknown_ui", "session_pending",
            "provider_login_required", "no_visible_editable_chat_input",
        ))

    async def _authenticate(self, page=None) -> None:
        self._set_auth_state("AUTHENTICATING", "provider_session_check")
        if page is None:
            page = await self.browser.primary_page(self.name)
        if getattr(page, "url", "") in {"", "about:blank"}:
            # This is only the initial blank-page bootstrap. Never navigate an
            # already loaded provider page, including one showing CAPTCHA.
            await page.goto(self.chat_url, wait_until="domcontentloaded")
        login = DeepSeekLogin(page, self.chat_url, timeout_ms=self.login_timeout_ms)
        try:
            await login.ensure_authenticated()
        except Exception as exc:
            if self.auth_state not in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
                message = str(exc)
                if "challenge_visible" in message:
                    self._set_auth_state("AUTHENTICATING", "challenge_visible")
                elif "unknown_ui" in message or "session_pending" in message:
                    self._set_auth_state("AUTHENTICATING", message.rsplit("(", 1)[-1].rstrip(")"))
                else:
                    self._set_auth_state("LOGIN_REQUIRED", "provider_login_required")
            raise exc
        self.ready = True
        self._set_auth_state("READY", "authenticated")
        self.last_error = None

    async def complete(self, prompt: str, conversation_id: str | None = None) -> str:
        async with self._request_lock:
            attempts = self.restart_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    page = await self._page_for_conversation(conversation_id)
                    self._mark_page_active(page)
                    try:
                        await self._authenticate(page)
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
                    if attempt >= attempts or self.auth_state == "LOGIN_INTERRUPTED" or self._is_auth_failure(exc):
                        raise
                    self._clear_conversation_pages()
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
                        await self._authenticate(page)
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
                    if attempt >= attempts or self._is_auth_failure(exc):
                        raise
                    self._clear_conversation_pages()
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
        self.chat_url = qwen_cfg.get("chat_url", "https://chat.qwen.ai/")
        self.browser = BrowserManager(
            profile_path=qwen_cfg.get("profile_dir", provider_profile_dir("qwen")),
            headless=qwen_cfg.get("headless", False),
            executable_path=browser_cfg.get("executable_path"),
            cdp_endpoint=browser_cfg.get("cdp_endpoint"),
            mode=browser_cfg.get("mode"),
            on_disconnect=self._on_browser_disconnect,
            launch_url=self.chat_url,
        )
        self.timeout_ms = int(qwen_cfg.get("timeout_ms", 180000))
        self.restart_retries = int(browser_cfg.get("restart_retries", 1))
        self.last_error: str | None = None
        self.ready = False
        self.auth_state = "STARTING"
        self.reason_code = "starting"
        self.updated_at = time.time()
        self.login_attempt_id = None
        self._conversation_pages: dict[str, object] = {}
        self.max_pages = browser_cfg.get("max_pages", 8)
        self.idle_timeout_ms = browser_cfg.get("idle_timeout_ms", 300000)
        self._page_records: dict[int, _PageRecord] = {}
        self._active_pages: set[int] = set()
        self._request_lock = asyncio.Lock()
        self._auth_watch_task: asyncio.Task | None = None
        self._auth_generation = 0
        self._watcher_running = False
        self._last_probe_at: float | None = None
        self._last_probe_result: str | None = None
        self._initial_start_available = True

    async def start(self) -> None:
        if self.auth_state == "LOGIN_INTERRUPTED":
            raise RuntimeError("login_interrupted: use retry_login explicitly")
        if not self._initial_start_available:
            raise RuntimeError("login attempt already started; use retry_login explicitly")
        self._initial_start_available = False
        self.login_attempt_id = uuid.uuid4().hex
        self.browser.set_diagnostics(provider=self.name, login_attempt_id=self.login_attempt_id, auth_state=self.auth_state)
        self._set_auth_state("CHECKING_SESSION", "checking_session")
        self._auth_generation += 1
        try:
            await self._authenticate()
        except Exception:
            if self.auth_state not in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
                self._start_auth_watcher(self._auth_generation)
            raise

    def _start_auth_watcher(self, generation: int) -> None:
        if self.auth_state in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
            return
        if self._auth_watch_task is None or self._auth_watch_task.done():
            self._auth_watch_task = asyncio.create_task(self._watch_auth(generation))

    def _cancel_auth_watcher(self) -> None:
        task = self._auth_watch_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._watcher_running = False

    async def _watch_auth(self, generation: int) -> None:
        self._watcher_running = True
        ready_streak = 0
        probe = None
        try:
            while generation == self._auth_generation and self.auth_state not in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
                page = self.browser._primary_pages.get(self.name)
                if page is None:
                    page = next((candidate for (owner, _), candidate in self.browser._page_claims.items() if owner == self.name), None)
                if page is None or page.is_closed() or not self.browser.is_running:
                    return
                if probe is None or probe.page is not page:
                    probe = QwenChat(page)
                state = await probe.probe_auth()
                self._last_probe_at = time.time()
                self._last_probe_result = state
                if state == CHAT_READY:
                    ready_streak += 1
                    if ready_streak >= 2:
                        self._set_auth_state("HANDOFF", "chat_ready")
                        if not self.browser.headless:
                            async def auth_probe(candidate) -> bool:
                                return await QwenChat(candidate).probe_auth() == CHAT_READY
                            await self.browser.handoff_to_headless(auth_probe=auth_probe)
                        self._set_auth_state("VERIFYING_SESSION", "session_probe")
                        if await QwenChat(await self.browser.page()).probe_auth() == CHAT_READY:
                            self.ready = True
                            self._set_auth_state("READY", "authenticated")
                            return
                else:
                    ready_streak = 0
                    if state == SIGN_IN_VISIBLE:
                        self._set_auth_state("LOGIN_REQUIRED", "provider_login_required")
                    elif state == CHALLENGE_VISIBLE:
                        self._set_auth_state("LOGIN_REQUIRED", "challenge_visible")
                    elif state == UNKNOWN_UI:
                        self._set_auth_state("AUTHENTICATING", "unknown_ui")
                    else:
                        self._set_auth_state("AUTHENTICATING", "session_pending")
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Qwen auth watcher failed")
        finally:
            self._watcher_running = False

    def _on_browser_disconnect(self, reason: str) -> None:
        if self.auth_state in {"STARTING", "CHECKING_SESSION", "LOGIN_REQUIRED", "AUTHENTICATING", "HANDOFF", "VERIFYING_SESSION"}:
            if not self.browser.lifecycle_events or self.browser.lifecycle_events[-1].get("reason") != reason:
                self.browser._emit_lifecycle("auth.interrupted", initiator="external", reason=reason)
            self._set_auth_state("LOGIN_INTERRUPTED", reason)
            self._cancel_auth_watcher()

    async def retry_login(self) -> None:
        self._cancel_auth_watcher()
        self._initial_start_available = True
        self.ready = False
        self.last_error = None
        self._set_auth_state("STARTING", "explicit_retry")
        await self.start()

    async def stop(self) -> None:
        self._cancel_auth_watcher()
        await self.browser.stop()
        self.ready = False
        self._conversation_pages.clear()
        self._page_records.clear()
        self._active_pages.clear()

    async def status(self) -> dict:
        return {
            "browser_running": self.browser.is_running,
            "browser_mode": self.browser.mode,
            "browser_ownership": "user" if self.browser.mode == "cdp" else "managed",
            "provider": self.name,
            "ready": self.ready,
            "last_error": self.last_error,
            "conversations": len(self._conversation_pages),
            "unique_pages": len({id(page) for page in self._conversation_pages.values()}),
            "conversation_aliases": len(self._conversation_pages),
            "state": self.auth_state,
            "reason_code": self.reason_code,
            "updated_at": self.updated_at,
            "login_attempt_id": self.login_attempt_id,
            "watcher_running": self._watcher_running,
            "last_probe_at": self._last_probe_at,
            "last_probe_result": self._last_probe_result,
            "browser_generation": self.browser.browser_generation,
            "lifecycle_events": list(self.browser.lifecycle_events),
            "last_lifecycle_event": self.browser.lifecycle_events[-1] if self.browser.lifecycle_events else None,
        }

    def _set_auth_state(self, state: str, reason_code: str) -> None:
        self.auth_state = state
        self.reason_code = reason_code
        self.updated_at = time.time()
        self.browser.set_diagnostics(provider=self.name, login_attempt_id=self.login_attempt_id, auth_state=state)
        self.browser._emit_lifecycle(
            "auth.ready" if state == "READY" else "auth.state",
            initiator="wmadapter",
            reason=reason_code,
        )

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
        if self.auth_state in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
            raise RuntimeError("provider_login_required")
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
        self._set_auth_state("AUTHENTICATING", "provider_session_check")
        page = await self._page_for_conversation(conversation_id)
        chat = QwenChat(page, timeout_ms=self.timeout_ms)
        # Keep the provider API compatibility hook; QwenChat implements it via
        # the structured probe and it performs no navigation.
        if await chat.is_authenticated():
            self.ready = True
            self._set_auth_state("READY", "authenticated")
            self.last_error = None
            return
        state = await chat.probe_auth()
        if state == CHAT_READY and await chat.probe_auth() == CHAT_READY:
            self.ready = True
            self._set_auth_state("READY", "authenticated")
            self.last_error = None
            return
        reason = {
            SIGN_IN_VISIBLE: "provider_login_required",
            CHALLENGE_VISIBLE: "challenge_visible",
            UNKNOWN_UI: "unknown_ui",
        }.get(state, "session_pending")
        if self.auth_state not in {"LOGIN_INTERRUPTED", "LOGIN_CANCELLED"}:
            self._set_auth_state("LOGIN_REQUIRED" if state == SIGN_IN_VISIBLE else "AUTHENTICATING", reason)
        raise RuntimeError(f"Qwen authentication is pending ({reason})")

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
                    if attempt >= attempts or self.auth_state == "LOGIN_INTERRUPTED":
                        raise
                    self._conversation_pages.clear()
                    await self.browser.restart()
            raise RuntimeError("Qwen request failed")

    async def stream_complete(self, prompt: str, conversation_id: str | None = None):
        yield await self.complete(prompt, conversation_id=conversation_id)
