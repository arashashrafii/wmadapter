from __future__ import annotations

import asyncio
import inspect

from playwright.async_api import Page
from ...browser.elements import first_visible

from .images import validate_artifact_url, validate_image_bytes
from .image_contract import QWEN_IMAGE_MODEL, qwen_image_aspect_for_size
from .selectors import CHAT_INPUTS, IMAGE_ARTIFACTS, RESPONSE_BLOCKS
from ..deepseek.login import CHAT_READY, CHALLENGE_VISIBLE, SIGN_IN_VISIBLE, SESSION_PENDING, UNKNOWN_UI
from ..submit import PreSubmitError, SubmitState, UncertainSubmitError


class QwenChat:
    def __init__(self, page: Page, timeout_ms: int = 180000):
        self.page = page
        self.timeout_ms = timeout_ms
        self.submit_state = SubmitState.NOT_SUBMITTED
        self._ready_probe_streak = 0
        self._previous_counts: dict[str, int] = {}

    async def _first_visible(self, selectors: list[str]):
        return await first_visible(self.page, selectors, "Qwen")

    async def _locator(self, root, selector: str):
        locator = root.locator(selector)
        return await locator if inspect.isawaitable(locator) else locator

    async def probe_auth(self) -> str:
        """Read current DOM state without navigation or submission."""
        for selector in ("iframe[src*='captcha']", "[id*='captcha' i]", "[class*='challenge' i]"):
            try:
                locator = await self._locator(self.page, selector)
                if await locator.last.is_visible(timeout=300):
                    self._ready_probe_streak = 0
                    return CHALLENGE_VISIBLE
            except Exception:
                pass
        # Qwen renders the composer even while the landing page is logged out.
        # Check visible authentication controls before treating that composer
        # as proof of an authenticated session.
        for selector in ("button", "a", "[role='button']"):
            try:
                controls = await self._locator(self.page, selector)
                count = await controls.count()
                for index in range(count):
                    control = controls.nth(index)
                    if await control.is_visible(timeout=300):
                        text = (await control.inner_text()).strip().lower()
                        if text in {"log in", "login", "sign in", "sign up"}:
                            self._ready_probe_streak = 0
                            return SIGN_IN_VISIBLE
            except Exception:
                pass
        try:
            field = await self._first_visible(CHAT_INPUTS)
            if await field.is_editable(timeout=300):
                self._ready_probe_streak += 1
                if self._ready_probe_streak >= 2:
                    return CHAT_READY
                return SESSION_PENDING
        except Exception:
            pass
        self._ready_probe_streak = 0
        locator = await self._locator(self.page, "text=/sign in|log in|login/i")
        count = locator.count()
        count = await count if inspect.isawaitable(count) else count
        return SIGN_IN_VISIBLE if count else UNKNOWN_UI

    async def is_authenticated(self) -> bool:
        return await self.probe_auth() == CHAT_READY

    async def _response_counts(self) -> dict[str, int]:
        return {selector: await self.page.locator(selector).count() for selector in RESPONSE_BLOCKS}

    async def latest_image_artifact(self) -> tuple[bytes, str]:
        """Fetch the latest rendered Qwen image after validating its source."""
        candidates = []
        for selector in IMAGE_ARTIFACTS:
            locator = self.page.locator(selector)
            for index in range(await locator.count()):
                source = await locator.nth(index).get_attribute("src")
                if source:
                    candidates.append(source)
        if not candidates:
            raise ValueError("Qwen image artifact was not rendered")
        source = validate_artifact_url(candidates[-1])
        request = self.page.context.request
        response = await request.get(source, timeout=self.timeout_ms)
        if not response.ok:
            raise ValueError("Qwen image artifact download failed")
        content_type = response.headers.get("content-type")
        content = await response.body()
        return validate_image_bytes(content, content_type)

    async def _select_image_aspect(self, size: str) -> None:
        if size == "auto":
            return
        aspect = qwen_image_aspect_for_size(size)
        current = self.page.get_by_text("Auto", exact=True).last
        if not await current.count() or not await current.is_visible(timeout=500):
            raise PreSubmitError("Qwen image aspect-ratio control is unavailable")
        await current.click()
        option = self.page.get_by_text(aspect, exact=True).last
        if not await option.count() or not await option.is_visible(timeout=500):
            raise PreSubmitError("Qwen image aspect-ratio option is unavailable")
        await option.click()

    async def send_image(
        self, prompt: str, *, size: str = "auto", model: str = QWEN_IMAGE_MODEL,
    ) -> tuple[bytes, str]:
        """Submit an image prompt and wait for a rendered artifact."""
        if model not in {"qwen-chat", QWEN_IMAGE_MODEL}:
            raise PreSubmitError("Qwen image model is unsupported")
        mode_button = self.page.get_by_role("button", name="Select Mode", exact=True)
        if await mode_button.count() and await mode_button.is_visible(timeout=500):
            await mode_button.click()
            image_mode = self.page.get_by_text("Create Image", exact=True).last
            if not await image_mode.count():
                raise PreSubmitError("Qwen image-generation mode is unavailable")
            await image_mode.click()
        await self._select_image_aspect(size)
        input_box = await self._first_visible(CHAT_INPUTS)
        await input_box.click()
        await input_box.fill(prompt)
        await input_box.press("Enter")
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            try:
                return await self.latest_image_artifact()
            except ValueError:
                await asyncio.sleep(1)
        raise TimeoutError("Qwen image artifact was not rendered before timeout")

    async def _latest_response_text(self, previous_counts: dict[str, int]) -> str:
        for selector in RESPONSE_BLOCKS:
            blocks = self.page.locator(selector)
            count = await blocks.count()
            previous_count = previous_counts.get(selector, 0)
            if count > previous_count:
                text = (await blocks.last.inner_text()).strip()
                if text:
                    return self._clean_response(text)
        return ""

    def _clean_response(self, text: str) -> str:
        if "Thinking completed" in text:
            text = text.split("Thinking completed", 1)[-1].strip()
        if "AI-generated content may not be accurate." in text:
            text = text.split("AI-generated content may not be accurate.", 1)[0].strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        noise = {"Auto", "How can I help you ?", "Qwen3.7-Plus"}
        lines = [line for line in lines if line not in noise]
        return "\n".join(lines).strip()

    async def send_message(self, message: str) -> str:
        self.submit_state = SubmitState.NOT_SUBMITTED
        try:
            input_box = await self._first_visible(CHAT_INPUTS)
            await self.page.wait_for_timeout(1000)
            previous_counts = await self._response_counts()
            self._previous_counts = previous_counts

            await input_box.click()
            await input_box.fill(message)
            await self.page.wait_for_timeout(300)
            self.submit_state = SubmitState.SUBMITTING
            self.submit_state = SubmitState.SUBMITTED_UNCERTAIN
            await input_box.press("Enter")

            deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
            last_text = ""
            stable_rounds = 0

            while asyncio.get_running_loop().time() < deadline:
                text = await self._latest_response_text(previous_counts)
                if text:
                    if text == last_text:
                        stable_rounds += 1
                    else:
                        stable_rounds = 0
                        last_text = text
                    if stable_rounds >= 2:
                        self.submit_state = SubmitState.COMPLETED
                        return text
                await asyncio.sleep(1)

            raise TimeoutError("Qwen response was not detected before timeout")
        except UncertainSubmitError:
            raise
        except Exception:
            if self.submit_state in (SubmitState.SUBMITTING, SubmitState.SUBMITTED_UNCERTAIN):
                # Do not expose provider response text, URLs, or filenames in
                # errors that may be retained or logged by the gateway.
                raise UncertainSubmitError("Qwen submission outcome is uncertain") from None
            raise PreSubmitError("Qwen request could not be submitted") from None

    async def recover_response(self, timeout_ms: int = 120000) -> str | None:
        """Reconcile the existing page after an ambiguous submission.

        This method only observes the current conversation; it never edits or
        resends the input, so a late answer cannot duplicate a user turn.
        """
        if timeout_ms <= 0 or self.submit_state not in (
            SubmitState.SUBMITTING, SubmitState.SUBMITTED_UNCERTAIN
        ):
            return None
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        last_text = ""
        stable_rounds = 0
        try:
            while asyncio.get_running_loop().time() < deadline:
                text = await self._latest_response_text(self._previous_counts)
                if text:
                    if text == last_text:
                        stable_rounds += 1
                    else:
                        stable_rounds = 0
                        last_text = text
                    if stable_rounds >= 2:
                        self.submit_state = SubmitState.COMPLETED
                        return text
                await asyncio.sleep(1)
        except Exception:
            return None
        return None
