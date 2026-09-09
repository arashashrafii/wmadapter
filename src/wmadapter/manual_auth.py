from __future__ import annotations

from dataclasses import dataclass
import asyncio
import inspect
import os
import socket
import subprocess
from typing import Any
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

from .browser.manager import BrowserManager
from .config import canonical_path, load_config, provider_profile_dir
from .providers.deepseek.login import CHAT_READY, RATE_LIMITED, DeepSeekLogin
from .providers.qwen.chat import QwenChat


@dataclass(frozen=True)
class AuthTarget:
    url: str
    profile_dir: str
    google_selectors: tuple[str, ...]


AUTH_TARGETS = {
    "deepseek": AuthTarget(
        url="https://chat.deepseek.com/",
        profile_dir=provider_profile_dir("deepseek"),
        google_selectors=(),
    ),
    "qwen": AuthTarget(
        url="https://chat.qwen.ai/",
        profile_dir=provider_profile_dir("qwen"),
        google_selectors=(
            'button:has-text("Google")',
            'div[role=button]:has-text("Google")',
            'a:has-text("Google")',
            '[aria-label*="Google"]',
            '[data-testid*="google"]',
        ),
    ),
}
_AUTH_TASKS: dict[int, asyncio.Task] = {}
_DEEPSEEK_PROBES: dict[int, DeepSeekLogin] = {}


async def _click_first_visible(page, selectors: tuple[str, ...], timeout: int = 1500) -> bool:
    for selector in selectors:
        locator = page.locator(selector).last
        try:
            if await locator.is_visible(timeout=timeout):
                await locator.click()
                return True
        except Exception:
            continue
    return False


async def _authenticated(provider: str, page, target: AuthTarget) -> bool:
    return await _probe_auth(provider, page, target) == CHAT_READY


async def _stable_auth_probe(provider: str, page, target: AuthTarget) -> bool:
    """Require two ready observations after a fresh page's warm-up probe."""
    states = [await _probe_auth(provider, page, target) for _ in range(3)]
    return all(state == CHAT_READY for state in states[-2:])


async def _probe_auth(provider: str, page, target: AuthTarget) -> str:
    if provider == "deepseek":
        probe = _DEEPSEEK_PROBES.get(id(page))
        if probe is None:
            probe = DeepSeekLogin(page, target.url)
            _DEEPSEEK_PROBES[id(page)] = probe
        return await probe.probe_auth()
    return await QwenChat(page).probe_auth()


async def _probe_context_auth(provider: str, context, target: AuthTarget) -> tuple[str | None, object | None]:
    """Probe every page because Google sign-in may return through a popup."""
    for page in list(context.pages):
        try:
            if page.is_closed():
                continue
            state = await _probe_auth(provider, page, target)
        except Exception:
            # OAuth popups can disappear while their opener is being updated.
            # Keep probing the remaining pages in the isolated context.
            continue
        if state == CHAT_READY:
            return state, page
    return None, None


async def _wait_for_auth(
    provider: str,
    page,
    target: AuthTarget,
    timeout_s: int = 300,
    interruption: dict[str, str | None] | None = None,
) -> None:
    key = id(page)
    existing = _AUTH_TASKS.get(key)
    if existing is not None:
        await asyncio.shield(existing)
        return
    task = asyncio.create_task(_wait_for_auth_once(provider, page, target, timeout_s, interruption))
    _AUTH_TASKS[key] = task
    try:
        await asyncio.shield(task)
    finally:
        if _AUTH_TASKS.get(key) is task:
            _AUTH_TASKS.pop(key, None)
        _DEEPSEEK_PROBES.pop(key, None)


async def _wait_for_auth_once(
    provider: str,
    page,
    target: AuthTarget,
    timeout_s: int,
    interruption: dict[str, str | None] | None = None,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    ready_streak = 0
    while asyncio.get_running_loop().time() < deadline:
        if interruption and interruption.get("state") == "LOGIN_INTERRUPTED":
            reason = interruption.get("reason") or "browser_disconnected"
            raise RuntimeError(f"LOGIN_INTERRUPTED (login cancelled): {provider} {reason}")
        closed = page.is_closed()
        if inspect.isawaitable(closed):
            closed = await closed
        if closed:
            raise RuntimeError(f"LOGIN_INTERRUPTED (login cancelled): {provider} browser page was closed")
        try:
            state = await _probe_auth(provider, page, target)
            if state == CHAT_READY:
                ready_streak += 1
                if ready_streak >= 2:
                    return
            else:
                ready_streak = 0
        except Exception:
            pass
        await asyncio.sleep(2)
    probe = _DEEPSEEK_PROBES.get(id(page))
    diagnostic = getattr(probe, "last_probe_diagnostic", None)
    detail = f"; last_probe={diagnostic}" if diagnostic else ""
    raise RuntimeError(f"Timed out waiting for {provider} authentication; login remains user-driven{detail}")


async def run_manual_auth(
    provider: str,
    *,
    use_google: bool = False,
    external_browser: bool = False,
    executable_path: str | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    if provider not in AUTH_TARGETS:
        raise RuntimeError(f"Unsupported manual auth provider: {provider}")

    config = config or load_config()
    browser_cfg = config["browser"]
    if browser_cfg.get("mode", "managed") == "cdp":
        raise RuntimeError("Manual authentication requires browser.mode=managed; CDP mode is attach-only")
    target = AUTH_TARGETS[provider]
    configured_url = config.get(provider, {}).get("chat_url", target.url)
    configured_parts = urlsplit(configured_url)
    target_parts = urlsplit(target.url)
    if (
        not isinstance(configured_url, str)
        or configured_parts.scheme != target_parts.scheme
        or configured_parts.hostname != target_parts.hostname
    ):
        raise RuntimeError(f"{provider}.chat_url must use the {target_parts.hostname} provider origin")
    target = AuthTarget(configured_url, target.profile_dir, target.google_selectors)
    profile_value = browser_cfg.get("profile_dir", provider_profile_dir(provider))
    if provider == "qwen":
        profile_value = config.get("qwen", {}).get("profile_dir", provider_profile_dir("qwen"))
    configured_executable = browser_cfg.get("executable_path")
    configured_executable = canonical_path(configured_executable) if configured_executable else None
    override_executable = canonical_path(executable_path) if executable_path else None
    if override_executable != configured_executable and executable_path is not None:
        raise RuntimeError(
            "--executable-path does not match browser.executable_path; configure the Gateway executable first"
        )
    interruption: dict[str, str | None] = {"state": None, "reason": None}
    if external_browser:
        executable = configured_executable
        if not executable:
            raise RuntimeError("External authentication requires browser.executable_path")
        profile = canonical_path(profile_value)
        with socket.socket() as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            cdp_port = probe_socket.getsockname()[1]
        process = subprocess.Popen(
            [executable, f"--user-data-dir={profile}", f"--app={target.url}", "--no-first-run", "--disable-sync", f"--remote-debugging-port={cdp_port}"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        await asyncio.sleep(0.75)
        if process.poll() is not None:
            details = b""
            if process.stderr is not None:
                details = await asyncio.to_thread(process.stderr.read)
            detail = details.decode("utf-8", errors="replace").strip()[-500:]
            raise RuntimeError(f"Isolated Chromium exited before login started: {detail or 'no diagnostic output'}")
        print(
            f"Complete {provider} authentication in the isolated Chromium window; it will close automatically."
        )
        playwright = await async_playwright().start()
        connected = None
        try:
            deadline = asyncio.get_running_loop().time() + 300
            while connected is None and asyncio.get_running_loop().time() < deadline:
                try:
                    connected = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                except Exception:
                    await asyncio.sleep(0.5)
            if connected is None:
                raise RuntimeError("Could not connect to the isolated Chromium login window")
            contexts = connected.contexts
            if not contexts or not contexts[0].pages:
                raise RuntimeError("The isolated Chromium login window has no page")
            context = contexts[0]
            while asyncio.get_running_loop().time() < deadline:
                state, page = await _probe_context_auth(provider, context, target)
                if state == CHAT_READY:
                    break
                if state == RATE_LIMITED:
                    raise RuntimeError(
                        f"{provider} rejected the authentication callback with TOO MANY REQUESTS (40029); "
                        "wait before retrying and do not repeat the login flow"
                    )
                await asyncio.sleep(2)
            else:
                diagnostic = None
                for candidate in list(context.pages):
                    probe = _DEEPSEEK_PROBES.get(id(candidate))
                    if probe is not None:
                        diagnostic = getattr(probe, "last_probe_diagnostic", None)
                        break
                raise RuntimeError(f"Timed out waiting for {provider} authentication; last_probe={diagnostic}")
            # Explicitly close the persistent context first so Chromium flushes
            # OAuth cookies/storage before the external process is terminated.
            try:
                await context.close()
            finally:
                await connected.close()
        finally:
            await playwright.stop()
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
            await asyncio.sleep(0.5)
        os.environ.pop("WMADAPTER_LOGIN", None)
        browser = BrowserManager(
            profile_path=profile,
            headless=True,
            executable_path=executable,
            launch_url=target.url,
        )
        try:
            page = await browser.primary_page(provider)
            if not await _stable_auth_probe(provider, page, target):
                raise RuntimeError(f"{provider} authentication was not detected in the external browser profile")
        finally:
            await browser.stop()
        return
    browser = BrowserManager(
        profile_path=canonical_path(profile_value),
        headless=False,
        executable_path=configured_executable,
        launch_url=target.url,
        on_disconnect=lambda reason: interruption.update(state="LOGIN_INTERRUPTED", reason=reason),
    )
    try:
        page = await browser.primary_page(provider)
        current_url = getattr(page, "url", "")
        if not isinstance(current_url, str) or not current_url.startswith(target.url.rstrip("/")):
            await page.goto(target.url, wait_until="domcontentloaded")
        if use_google:
            clicked = await _click_first_visible(page, target.google_selectors)
            if not clicked:
                print("Google sign-in button was not detected automatically. Click it manually in the browser.")
        print(f"Complete {provider} authentication in the opened browser; Web Model Adapter will continue automatically.")
        await _wait_for_auth(provider, page, target, interruption=interruption)
        async def auth_probe(page) -> bool:
            return await _stable_auth_probe(provider, page, target)

        await browser.handoff_to_headless(auth_probe=auth_probe)
        await browser.stop()
    finally:
        await browser.stop()
