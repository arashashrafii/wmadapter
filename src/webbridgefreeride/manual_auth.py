from __future__ import annotations

from dataclasses import dataclass
import shutil

from .browser.manager import BrowserManager


@dataclass(frozen=True)
class AuthTarget:
    url: str
    profile_dir: str
    google_selectors: tuple[str, ...]


AUTH_TARGETS = {
    "deepseek": AuthTarget(
        url="https://chat.deepseek.com/",
        profile_dir=".webbridge-profile",
        google_selectors=(),
    ),
    "qwen": AuthTarget(
        url="https://chat.qwen.ai/",
        profile_dir=".webbridge-profile/qwen",
        google_selectors=(
            'button:has-text("Google")',
            'div[role=button]:has-text("Google")',
            'a:has-text("Google")',
            '[aria-label*="Google"]',
            '[data-testid*="google"]',
        ),
    ),
}


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


async def run_manual_auth(
    provider: str,
    *,
    use_google: bool = False,
    executable_path: str | None = None,
) -> None:
    if provider not in AUTH_TARGETS:
        raise RuntimeError(f"Unsupported manual auth provider: {provider}")

    target = AUTH_TARGETS[provider]
    if executable_path is None:
        for browser in ("google-chrome", "chromium", "chromium-browser"):
            executable_path = shutil.which(browser)
            if executable_path:
                break
    browser = BrowserManager(
        profile_path=target.profile_dir,
        headless=False,
        executable_path=executable_path,
    )
    try:
        page = await browser.page()
        await page.goto(target.url, wait_until="domcontentloaded")
        if use_google:
            clicked = await _click_first_visible(page, target.google_selectors)
            if not clicked:
                print("Google sign-in button was not detected automatically. Click it manually in the browser.")
        print(f"Complete {provider} authentication in the opened browser.")
        print("Press Enter here after the chat page is logged in and usable.")
        input()
    finally:
        await browser.stop()
