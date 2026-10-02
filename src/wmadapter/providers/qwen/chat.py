from __future__ import annotations

import asyncio
import inspect
import re
from urllib.parse import urlparse

from playwright.async_api import Page
from ...browser.elements import first_visible

from .images import validate_artifact_url, validate_image_bytes
from .image_contract import QWEN_IMAGE_MODEL, qwen_image_aspect_for_size, qwen_image_ui_label
from .selectors import CHAT_INPUTS, IMAGE_ARTIFACTS, RESPONSE_BLOCKS
from ..deepseek.login import CHAT_READY, CHALLENGE_VISIBLE, SIGN_IN_VISIBLE, SESSION_PENDING, UNKNOWN_UI
from ..submit import PreSubmitError, SubmitState, UncertainSubmitError


# The composer footer's aspect-ratio dropdown renders the current preset, for
# example "16:9". The footer also holds the Create Image model dropdown, whose
# label looks like "Qwen-Image 2.0".
# Qwen-Image 3 exposes an "Auto" ratio; Qwen-Image 2.0 shows a concrete preset.
RATIO_LABEL_PATTERN = re.compile(r"^(?:\d+\s*:\s*\d+|auto)$", re.IGNORECASE)
IMAGE_MODEL_LABEL_PATTERN = re.compile(r"^qwen-image\b", re.IGNORECASE)
SUBMODE_SELECTS = (
    ".message-input-column-footer-submode .qwen-chat-v2-dropdown-menu-select"
)

# A finished Create Image result is rendered as a CSS background-image on a
# conversation container, not as an <img> element, and Qwen's generated outputs
# live under this path. Both are observed facts from the live Create Image flow.
GENERATED_ARTIFACT_MARKER = "/image_gen/"
BACKGROUND_IMAGE_PROBE = """() => {
  const root = document.querySelector('#chat-message-container');
  if (!root) return [];
  const found = [];
  for (const el of root.querySelectorAll('*')) {
    const bg = getComputedStyle(el).backgroundImage;
    if (!bg || bg === 'none') continue;
    for (const part of bg.split(',')) {
      const match = part.match(/url\\(\\s*["']?([^"')]+)["']?\\s*\\)/);
      if (match && match[1]) found.push(match[1]);
    }
  }
  return found;
}"""


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

    async def image_artifact_sources(self) -> set[str]:
        """Currently rendered provider-owned image sources.

        The Create Image page preloads template thumbnails from the same
        provider CDN as real artifacts, so callers snapshot this before
        submitting and only accept a newly appearing source.

        A finished Create Image result is rendered as a CSS ``background-image``
        on a container rather than as an ``<img>`` element, so computed styles
        are inspected in addition to image elements. Both channels are scoped to
        the conversation container and filtered by the provider host allowlist
        later, in :meth:`latest_image_artifact`.
        """
        sources: set[str] = set()
        for selector in IMAGE_ARTIFACTS:
            locator = self.page.locator(selector)
            for index in range(await locator.count()):
                source = await locator.nth(index).get_attribute("src")
                if source:
                    sources.add(source)
        sources.update(await self._background_image_sources())
        return sources

    async def _background_image_sources(self) -> list[str]:
        """Computed ``background-image`` URLs inside the conversation container."""
        urls = await self.page.evaluate(BACKGROUND_IMAGE_PROBE)
        return [url for url in (urls or []) if isinstance(url, str) and url]

    async def latest_image_artifact(self, exclude: set[str] | None = None) -> tuple[bytes, str]:
        """Fetch the latest rendered Qwen image after validating its source."""
        known = exclude or set()
        # A source that is not a provider-owned HTTPS artifact yet (for example
        # an inline data: URL while the image is still materialising) is skipped
        # rather than failing the whole attempt.
        allowed: list[str] = []
        for source in await self.image_artifact_sources():
            if source in known:
                continue
            try:
                allowed.append(validate_artifact_url(source))
            except ValueError:
                continue
        if not allowed:
            raise ValueError("Qwen image artifact was not rendered")
        # Prefer the provider's generated-output path so a decorative
        # provider-hosted background cannot mask the real artifact.
        generated = [source for source in allowed if GENERATED_ARTIFACT_MARKER in source]
        source = (generated or allowed)[-1]
        request = self.page.context.request
        response = await request.get(source, timeout=self.timeout_ms)
        if not response.ok:
            raise ValueError("Qwen image artifact download failed")
        content_type = response.headers.get("content-type")
        content = await response.body()
        return validate_image_bytes(content, content_type)

    def _submode_selects(self):
        """Composer footer dropdowns (image model, aspect ratio)."""
        return self.page.locator(SUBMODE_SELECTS)

    async def _submode_labels(self) -> list[str]:
        selects = self._submode_selects()
        return [
            (await selects.nth(index).inner_text()).strip()
            for index in range(await selects.count())
        ]

    async def _footer_index_for(self, pattern: re.Pattern) -> int | None:
        """Index of the first footer dropdown whose label matches ``pattern``."""
        selects = self._submode_selects()
        for index in range(await selects.count()):
            label = (await selects.nth(index).inner_text()).strip()
            if pattern.match(label):
                return index
        return None

    async def _choose_footer_option(self, index: int, option: str) -> None:
        """Open one composer footer dropdown and pick ``option``.

        The option text is matched only outside the footer. Ratio tokens and
        model names also appear in the Create Image template gallery, and the
        footer itself shows the current value, so an unscoped text match can
        click a template card instead of the dropdown entry.
        """
        await self._submode_selects().nth(index).click()
        await asyncio.sleep(0.6)
        candidates = self.page.get_by_text(option, exact=True)
        pick = None
        for position in range(await candidates.count()):
            node = candidates.nth(position)
            try:
                if not await node.is_visible():
                    continue
                inside_footer = await node.evaluate(
                    "el => !!el.closest('.message-input-column-footer-submode')"
                )
            except Exception:  # noqa: BLE001 - detached nodes during re-render
                continue
            if inside_footer:
                continue
            pick = node
        if pick is None:
            raise PreSubmitError("Qwen image-generation option is unavailable")
        await pick.click()
        await asyncio.sleep(0.6)

    async def _wait_footer_index(
        self, pattern: re.Pattern, timeout_s: float = 10.0,
    ) -> int | None:
        """Wait for a composer footer dropdown whose label matches ``pattern``.

        Switching the Create Image model re-renders the composer footer, so the
        aspect-ratio dropdown can be briefly absent. Poll with a bounded
        deadline instead of failing on the first look.
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            index = await self._footer_index_for(pattern)
            if index is not None:
                return index
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.5)


    async def _in_image_mode(self) -> bool:
        # The aspect-ratio dropdown only exists in Create Image mode, so it is
        # a reliable mode indicator (the mode label itself is not a footer
        # dropdown).
        return await self._footer_index_for(RATIO_LABEL_PATTERN) is not None

    async def _select_image_model(self, model: str) -> None:
        """Select the requested image model in the Create Image footer."""
        desired = qwen_image_ui_label(model)
        index = await self._wait_footer_index(IMAGE_MODEL_LABEL_PATTERN)
        if index is None:
            raise PreSubmitError("Qwen image model control is unavailable")
        current = (await self._submode_selects().nth(index).inner_text()).strip()
        if current == desired:
            return
        await self._choose_footer_option(index, desired)
        # Confirm the switch took effect before touching the ratio dropdown.
        await self._wait_footer_index(RATIO_LABEL_PATTERN)

    async def _select_image_aspect(self, size: str) -> None:
        """Pick the Qwen Studio aspect-ratio preset for a normalized size.

        In Create Image mode the composer footer renders the image model and
        the aspect ratio. The ratio dropdown shows the currently selected
        preset (for example ``16:9``); the preset list only appears after it is
        clicked.
        """
        index = await self._wait_footer_index(RATIO_LABEL_PATTERN)
        if index is None:
            raise PreSubmitError("Qwen image aspect-ratio control is unavailable")
        if size == "auto":
            # The live UI has no Auto preset; leave the provider default.
            return
        desired = qwen_image_aspect_for_size(size)
        # "auto" is rendered capitalised in the Create Image dropdown.
        display = "Auto" if desired == "auto" else desired
        current = (await self._submode_selects().nth(index).inner_text()).strip()
        if current.replace(" ", "").lower() == display.lower().replace(" ", ""):
            return
        await self._choose_footer_option(index, display)
        # The footer can re-render after a model switch; confirm the ratio took
        # effect so a submission never silently uses the wrong shape.
        verify = await self._wait_footer_index(RATIO_LABEL_PATTERN)
        if verify is not None:
            applied = (await self._submode_selects().nth(verify).inner_text()).strip()
            if applied.replace(" ", "").lower() != display.lower().replace(" ", ""):
                raise PreSubmitError("Qwen image aspect-ratio was not applied")

    async def send_image(
        self, prompt: str, *, size: str = "auto", model: str = QWEN_IMAGE_MODEL,
    ) -> tuple[bytes, str]:
        """Submit an image prompt and wait for a rendered artifact."""
        if model not in {"qwen-chat", QWEN_IMAGE_MODEL}:
            raise PreSubmitError("Qwen image model is unsupported")
        await self._enter_image_mode()
        if model == QWEN_IMAGE_MODEL:
            # Qwen Studio's Create Image defaults to Qwen-Image 2.0, so the
            # requested Image 3 model must be selected explicitly.
            await self._select_image_model(model)
        await self._select_image_aspect(size)
        # Template thumbnails share the provider CDN with real artifacts; only
        # accept an image source that appears after this submission.
        preexisting = await self.image_artifact_sources()
        input_box = await self._first_visible(CHAT_INPUTS)
        await input_box.click()
        await input_box.fill(prompt)
        await input_box.press("Enter")
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            try:
                return await self.latest_image_artifact(exclude=preexisting)
            except ValueError:
                await asyncio.sleep(1)
        raise TimeoutError("Qwen image artifact was not rendered before timeout")

    async def _enter_image_mode(self) -> None:
        """Switch the Qwen composer into Create Image mode if it is not active."""
        if await self._in_image_mode():
            return
        # The mode selector is a div with aria-label="Select Mode"; clicking it
        # opens a portal menu whose items are not buttons.
        trigger = self.page.locator(".mode-select-open").last
        if not await trigger.count():
            raise PreSubmitError("Qwen image-generation mode is unavailable")
        await trigger.click()
        await asyncio.sleep(0.5)
        image_mode = self.page.get_by_text("Create Image", exact=True).last
        if not await image_mode.count() or not await image_mode.is_visible(timeout=1000):
            raise PreSubmitError("Qwen image-generation mode is unavailable")
        await image_mode.click()
        await asyncio.sleep(2)
        if not await self._in_image_mode():
            raise PreSubmitError("Qwen image-generation mode is unavailable")


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
