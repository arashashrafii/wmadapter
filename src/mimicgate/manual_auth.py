from __future__ import annotations

from dataclasses import dataclass
import asyncio
import inspect
from typing import Any

from .browser.manager import BrowserManager
from .config import canonical_path, load_config, provider_profile_dir
from .providers.deepseek.login import DeepSeekLogin
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
    if provider == "deepseek":
        return await DeepSeekLogin(page, target.url).is_authenticated()
    return await QwenChat(page).is_authenticated()


async def _wait_for_auth(provider: str, page, target: AuthTarget, timeout_s: int = 300) -> None:
    key = id(page)
    existing = _AUTH_TASKS.get(key)
    if existing is not None:
        await asyncio.shield(existing)
        return
    task = asyncio.create_task(_wait_for_auth_once(provider, page, target, timeout_s))
    _AUTH_TASKS[key] = task
    try:
        await asyncio.shield(task)
    finally:
        if _AUTH_TASKS.get(key) is task:
            _AUTH_TASKS.pop(key, None)


async def _wait_for_auth_once(provider: str, page, target: AuthTarget, timeout_s: int) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        closed = page.is_closed()
        if inspect.isawaitable(closed):
            closed = await closed
        if closed:
            raise RuntimeError(f"{provider} login cancelled: browser page was closed")
        try:
            if await _authenticated(provider, page, target):
                return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError(f"Timed out waiting for {provider} authentication; login remains user-driven")


async def run_manual_auth(
    provider: str,
    *,
    use_google: bool = False,
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
    browser = BrowserManager(
        profile_path=canonical_path(profile_value),
        headless=False,
        executable_path=configured_executable,
        launch_url=target.url,
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
        print(f"Complete {provider} authentication in the opened browser; MimicGate will continue automatically.")
        await _wait_for_auth(provider, page, target)
        async def auth_probe(page) -> bool:
            if provider == "deepseek":
                return await DeepSeekLogin(page, target.url).is_authenticated()
            return await QwenChat(page).is_authenticated()

        await browser.handoff_to_headless(auth_probe=auth_probe)
        await browser.stop()
    finally:
        await browser.stop()
