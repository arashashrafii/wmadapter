from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.async_api import Page
from ...browser.elements import first_visible

from .selectors import ATTACH_BUTTONS, CHAT_INPUTS, FILE_INPUTS, RESPONSE_BLOCKS, SEND_BUTTONS
from ..submit import PreSubmitError, SubmitState, UncertainSubmitError


class DeepSeekChat:
    def __init__(self, page: Page, timeout_ms: int = 180000):
        self.page = page
        self.timeout_ms = timeout_ms
        self.submit_state = SubmitState.NOT_SUBMITTED

    async def _first_visible(self, selectors: list[str]):
        return await first_visible(self.page, selectors, "DeepSeek")

    async def _response_locator(self):
        """Return one non-overlapping locator for the rendered answer blocks."""
        fallback = None
        for selector in RESPONSE_BLOCKS:
            locator = self.page.locator(selector)
            fallback = fallback or locator
            if await locator.count():
                return locator
        return fallback

    async def _response_text(self, block) -> str:
        """Read Mermaid source from DeepSeek's Code tab when available."""
        text = (await block.inner_text()).strip()
        if "Diagram" not in text or "Code" not in text:
            return text
        try:
            code_candidates = [
                self.page.get_by_role("button", name="Code", exact=True).last,
                self.page.get_by_role("tab", name="Code", exact=True).last,
                self.page.get_by_text("Code", exact=True).last,
            ]
            for code_button in code_candidates:
                if await code_button.count() and await code_button.is_visible(timeout=500):
                    await code_button.click()
                    break
            else:
                return text
            if code_button:
                await self.page.wait_for_timeout(250)
                source = ""
                source_blocks = self.page.locator("pre code")
                if await source_blocks.count():
                    source = (await source_blocks.last.inner_text()).strip()
                if not source:
                    source = (await block.inner_text()).strip()
                if source.startswith(("graph ", "flowchart ", "sequenceDiagram", "classDiagram", "stateDiagram",
                                      "erDiagram", "gantt", "pie", "journey", "mindmap", "timeline", "xychart-beta")):
                    return source
        except Exception:
            pass
        return text

    async def _enabled_send_button(self):
        for selector in SEND_BUTTONS:
            button = self.page.locator(selector).last
            try:
                if await button.is_visible(timeout=500) and await button.is_enabled():
                    return button
            except Exception:
                continue
        return None

    async def delete_remote_conversation(self) -> bool:
        """Delete the current DeepSeek Web conversation through its UI."""
        current_url = self.page.url
        path = urlparse(current_url).path
        if not path.startswith("/a/chat/s/"):
            return False
        href = path.split("?", 1)[0]
        chat_link = self.page.locator(f'a[href="{href}"]').last
        if not await chat_link.count():
            return False
        menu_button = chat_link.locator('[role="button"]').last
        if not await menu_button.count():
            return False
        await menu_button.click()
        await self.page.get_by_text("Delete", exact=True).last.click()
        await self.page.get_by_role("button", name="Delete chat", exact=True).click()
        await self.page.wait_for_timeout(500)
        return True

    async def _attach_data_images(self, attachments: list[str], directory: str) -> None:
        for index, data_url in enumerate(attachments):
            if not data_url.startswith("data:image/") or "," not in data_url:
                raise ValueError("Only data:image attachments are supported")
            header, encoded = data_url.split(",", 1)
            mime = header[5:].split(";", 1)[0]
            if not mime.startswith("image/"):
                raise ValueError("Only image attachments are supported")
            try:
                content = base64.b64decode(unquote(encoded), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid image data URL") from exc
            suffix = "." + mime.split("/", 1)[1].split("+", 1)[0]
            path = Path(directory) / f"attachment-{index}{suffix}"
            path.write_bytes(content)
            file_input = self.page.locator(FILE_INPUTS[0]).last
            if await file_input.count():
                await file_input.set_input_files(str(path), timeout=15000)
            else:
                button = await self._first_visible(ATTACH_BUTTONS)
                async with self.page.expect_file_chooser() as chooser_info:
                    await button.click()
                chooser = await chooser_info.value
                await chooser.set_files(str(path), timeout=15000)
            # DeepSeek clears the input after consuming the change event;
            # the caller keeps directory alive until the response completes.
            await self.page.wait_for_timeout(1500)

    async def send_message(self, message: str, attachments: list[str] | None = None) -> str:
        self.submit_state = SubmitState.NOT_SUBMITTED
        try:
            input_box = await self._first_visible(CHAT_INPUTS)
            response_locator = await self._response_locator()
        except Exception as exc:
            raise PreSubmitError(str(exc)) from exc
        previous_count = await response_locator.count()
        previous_text = ""
        if previous_count:
                    previous_text = await self._response_text(response_locator.last)

        attachment_directory = tempfile.TemporaryDirectory(prefix="wmadapter-image-") if attachments else None
        try:
            if attachments:
                await self._attach_data_images(attachments, attachment_directory.name)
            await input_box.fill(message)
            sent = False
            button = None
            if attachments:
                # The preview can appear before DeepSeek finishes processing
                # the upload. Its send control becomes enabled only afterward.
                upload_deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
                while asyncio.get_running_loop().time() < upload_deadline:
                    button = await self._enabled_send_button()
                    if button is not None:
                        break
                    remaining = upload_deadline - asyncio.get_running_loop().time()
                    await asyncio.sleep(min(10, max(0.1, remaining)))
            else:
                button = await self._enabled_send_button()
            if button is not None:
                self.submit_state = SubmitState.SUBMITTING
                self.submit_state = SubmitState.SUBMITTED_UNCERTAIN
                await button.click()
                sent = True
            if not sent:
                if attachments:
                    raise TimeoutError("DeepSeek send button did not become enabled after image upload")
                self.submit_state = SubmitState.SUBMITTING
                self.submit_state = SubmitState.SUBMITTED_UNCERTAIN
                await input_box.press("Enter")

            deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
            last_text = ""
            stable_rounds = 0

            while asyncio.get_running_loop().time() < deadline:
                blocks = await self._response_locator()
                block_count = await blocks.count()
                current_text = await self._response_text(blocks.last)
                if block_count > previous_count or (block_count and current_text != previous_text):
                    # Read the complete rendered block, including multiline Markdown
                    # and any content that arrived after the first DOM update.
                    text = current_text
                    if text:
                        if text == last_text:
                            stable_rounds += 1
                        else:
                            stable_rounds = 0
                            last_text = text
                        # A short pause is common while DeepSeek renders Markdown;
                        # require a longer stable window before returning the answer.
                        if stable_rounds >= 5:
                            self.submit_state = SubmitState.COMPLETED
                            return text
                await asyncio.sleep(1)

            raise TimeoutError("DeepSeek response was not detected before timeout")
        except UncertainSubmitError:
            raise
        except Exception as exc:
            if self.submit_state in (SubmitState.SUBMITTING, SubmitState.SUBMITTED_UNCERTAIN):
                raise UncertainSubmitError(str(exc)) from exc
            raise PreSubmitError(str(exc)) from exc
        finally:
            if attachment_directory is not None:
                attachment_directory.cleanup()
