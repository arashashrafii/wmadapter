from __future__ import annotations

import asyncio
import inspect
import re
from urllib.parse import urlparse

from playwright.async_api import Page
from ...browser.elements import first_visible

from .images import validate_artifact_url, validate_image_bytes
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

    async def delete_remote_conversation(self) -> bool:
        """Delete the current Qwen chat through the visible chat menu."""
        path = urlparse(self.page.url).path.rstrip("/")
        if not path.startswith("/c/"):
            return False
        # Qwen's sidebar links do not always carry an href.  In the current
        # SPA, the selected item is identified by the active class instead.
        link = self.page.locator(f'a[href="{path}"]').last
        if not await link.count():
            link = self.page.locator("a.chat-item-drag-link-active").last
        if not await link.count():
            return False
        container = link.locator("xpath=ancestor::div[contains(@class, 'chat-item-drag')][1]")
        buttons = container.locator("button")
        if not await buttons.count():
            return False
        await buttons.last.click()
        for label in ("Delete", "Delete chat", "Remove"):
            item = self.page.get_by_text(label, exact=True).last
            if await item.count() and await item.is_visible(timeout=500):
                await item.click()
                break
        else:
            return False
        for label in ("Delete", "Delete chat", "Confirm"):
            confirm = self.page.get_by_role("button", name=label, exact=True).last
            if await confirm.count() and await confirm.is_visible(timeout=500):
                await confirm.click()
                break
        await self.page.wait_for_timeout(500)
        return True

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

    async def send_image(self, prompt: str) -> tuple[bytes, str]:
        """Submit an image prompt and wait for a rendered artifact."""
        mode_button = self.page.get_by_role("button", name="Select Mode", exact=True)
        if await mode_button.count() and await mode_button.is_visible(timeout=500):
            await mode_button.click()
            image_mode = self.page.get_by_text("Create Image", exact=True).last
            if not await image_mode.count():
                raise PreSubmitError("Qwen image-generation mode is unavailable")
            await image_mode.click()
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
        noise = {
            "Auto",
            "How can I help you ?",
            "Qwen3.7-Plus",
            "Qwen3.8-Max",
            "Qwen3.8-Omni-Flash",
        }
        lines = [line for line in lines if line not in noise]
        return "\n".join(lines).strip()

    @staticmethod
    def _model_labels(model: str) -> tuple[str, ...]:
        parts = model.split("-")
        title = "-".join([parts[0].capitalize(), *[part.capitalize() for part in parts[1:]]])
        spaced = title.replace("-", " ")
        return tuple(dict.fromkeys((title, spaced, model)))

    async def _select_model(self, model: str | None) -> None:
        if not model or model == "qwen-chat":
            return
        labels = self._model_labels(model)
        visible_label = None
        model_trigger = self.page.locator("[aria-label='Select Model']").first
        try:
            await model_trigger.wait_for(state="visible", timeout=5000)
            visible_label = model_trigger
        except Exception:
            model_trigger = self.page.get_by_role("button", name="Select Model", exact=True)
            if await model_trigger.count() and await model_trigger.is_visible(timeout=500):
                visible_label = model_trigger

        triggers = self.page.locator("button, [role='button']")
        for index in range(await triggers.count()):
            if visible_label is not None:
                break
            candidate = triggers.nth(index)
            if not await candidate.is_visible(timeout=300):
                continue
            text = " ".join((await candidate.inner_text()).split())
            if re.match(r"^Qwen(?:[0-9]|\s*[0-9])", text, re.IGNORECASE):
                visible_label = candidate
                break
        if visible_label is None:
            raise PreSubmitError(f"Qwen model is not visible: {model}")

        # The current model label is also the selector trigger. Clicking it
        # opens the provider-owned model menu; the exact label then identifies
        # the requested option without relying on generated CSS class names.
        await visible_label.click()
        options = self.page.get_by_role("option")
        for label in labels:
            option = self.page.get_by_text(label, exact=True).last
            if await option.count() and await option.is_visible(timeout=1000):
                await option.click()
                return
            for index in range(await options.count()):
                option = options.nth(index)
                if not await option.is_visible(timeout=300):
                    continue
                option_text = " ".join((await option.inner_text()).split())
                if option_text == label or option_text.startswith(f"{label} "):
                    await option.click()
                    return
        raise PreSubmitError(f"Qwen model option is unavailable: {model}")

    async def send_message(self, message: str, model: str | None = None) -> str:
        self.submit_state = SubmitState.NOT_SUBMITTED
        try:
            await self._select_model(model)
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
        except (PreSubmitError, UncertainSubmitError):
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
