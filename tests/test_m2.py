from __future__ import annotations

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mimicgate.config import load_config
from mimicgate.credentials import CredentialStore
from mimicgate.security import redact
from mimicgate.providers.router import ProviderRouter
from mimicgate.main import Message, _clean_renderer_artifacts, _extract_tool_call, _fallback_conversation_id, _is_title_request, _local_title, _prompt
from mimicgate.ports import find_free_port
from mimicgate.manual_auth import AUTH_TARGETS, _stable_auth_probe, _wait_for_auth, run_manual_auth
from mimicgate.providers.base import ChatProvider
from mimicgate.providers.deepseek.chat import DeepSeekChat
from mimicgate.providers.deepseek.login import CHAT_READY, DeepSeekLogin
from mimicgate.browser.manager import BrowserManager
from mimicgate.service import AUTH_STATES, DeepSeekService, QwenService


class FakeProvider(ChatProvider):
    name = "fake"

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {"ready": True}

    async def complete(self, prompt: str, conversation_id: str | None = None) -> str:
        return prompt


class Milestone2Tests(unittest.TestCase):
    def test_headless_handoff_auth_probe_allows_new_page_warmup(self):
        page = Mock()
        target = AUTH_TARGETS["deepseek"]
        with patch(
            "mimicgate.manual_auth._probe_auth",
            new=AsyncMock(side_effect=["SESSION_PENDING", CHAT_READY, CHAT_READY]),
        ) as probe:
            self.assertTrue(asyncio.run(_stable_auth_probe("deepseek", page, target)))
        self.assertEqual(probe.await_count, 3)

    def test_deepseek_login_accepts_current_message_textarea(self):
        class Locator:
            def __init__(self, matches=False):
                self.matches = matches
                self.last = self

            async def is_visible(self, timeout=0):
                return self.matches

            async def is_editable(self, timeout=0):
                return self.matches

        class Page:
            url = "https://chat.deepseek.com/"
            frames = []

            def locator(self, selector):
                return Locator('textarea[placeholder*="Message"]' == selector)

        login = DeepSeekLogin(Page())
        self.assertEqual(asyncio.run(login.probe_auth()), "SESSION_PENDING")
        self.assertEqual(asyncio.run(login.probe_auth()), CHAT_READY)
        self.assertEqual(login.last_probe_diagnostic["reason"], "editable_chat_input")
        self.assertEqual(login.last_probe_diagnostic["selector"], 'textarea[placeholder*="Message"]')

    def test_deepseek_unknown_probe_exposes_input_diagnostics(self):
        class Locator:
            last = None
            async def count(self):
                return 0
            async def is_visible(self, timeout=0):
                return False
            async def is_editable(self, timeout=0):
                return False

        class Page:
            url = "https://chat.deepseek.com/error"
            frames = []
            def locator(self, selector):
                return Locator()

        login = DeepSeekLogin(Page())
        self.assertEqual(asyncio.run(login.probe_auth()), "UNKNOWN_UI")
        diagnostic = login.last_probe_diagnostic
        self.assertEqual(diagnostic["reason"], "no_visible_editable_chat_input")
        self.assertEqual(diagnostic["url"], "https://chat.deepseek.com/error")
        self.assertEqual(len(diagnostic["chat_inputs"]), 7)
        self.assertTrue(all(item["count"] == 0 for item in diagnostic["chat_inputs"]))

    def test_login_cancelled_is_stable_and_explicit_retry_is_single_attempt(self):
        service = DeepSeekService(load_config())
        service._on_browser_disconnect("browser_disconnected")
        self.assertEqual(service.auth_state, "LOGIN_INTERRUPTED")
        self.assertIn("LOGIN_INTERRUPTED", AUTH_STATES)
        service.start = AsyncMock()
        asyncio.run(service.retry_login())
        service.start.assert_awaited_once()
        self.assertEqual(service.auth_state, "STARTING")

    def test_cancelled_provider_does_not_launch_for_chat(self):
        service = DeepSeekService(load_config())
        service._set_auth_state("LOGIN_CANCELLED", "browser_disconnected")
        service.browser.page_for = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "provider_login_required"):
            asyncio.run(service._page_for_conversation(None))
        service.browser.page_for.assert_not_awaited()

    def test_disconnect_status_contains_terminal_diagnostics(self):
        service = DeepSeekService(load_config())
        service._on_browser_disconnect("playwright_disconnect")
        status = asyncio.run(service.status())
        self.assertEqual(status["state"], "LOGIN_INTERRUPTED")
        self.assertEqual(status["last_lifecycle_event"]["event_name"], "auth.state")
        self.assertEqual(status["last_lifecycle_event"]["reason"], "playwright_disconnect")
        for field in ("timestamp", "provider", "login_attempt_id", "browser_generation", "page_count", "auth_state", "initiator", "pid", "exit_status"):
            self.assertIn(field, status["last_lifecycle_event"])

    def test_qwen_disconnect_is_terminal_and_retry_is_explicit(self):
        service = QwenService(load_config())
        service._on_browser_disconnect("context_close")
        service.start = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "provider_login_required"):
            asyncio.run(service._page_for_conversation(None))
        asyncio.run(service.retry_login())
        service.start.assert_awaited_once()
        self.assertEqual(service.auth_state, "STARTING")

    def test_closed_login_page_stops_auth_watcher(self):
        page = Mock()
        page.is_closed.return_value = True
        with self.assertRaisesRegex(RuntimeError, "login cancelled"):
            asyncio.run(_wait_for_auth("deepseek", page, AUTH_TARGETS["deepseek"], timeout_s=120))

    def test_transport_disconnect_uses_crash_reason_only_with_process_exit(self):
        process = SimpleNamespace(pid=4321, returncode=137)
        manager = BrowserManager(launch_url="https://chat.deepseek.com/")
        manager.browser = SimpleNamespace(_impl_obj=SimpleNamespace(_process=process))
        manager.launch_info["pid"] = process.pid
        manager._mark_disconnected("playwright_disconnect")
        self.assertEqual(manager.lifecycle_events[-1]["reason"], "chromium_crash_or_oom")
        self.assertEqual(manager.lifecycle_events[-1]["exit_status"], 137)

        manager = BrowserManager(launch_url="https://chat.deepseek.com/")
        manager.browser = SimpleNamespace(_impl_obj=SimpleNamespace(_process=SimpleNamespace(pid=4322, returncode=None)))
        manager._mark_disconnected("playwright_disconnect")
        self.assertEqual(manager.lifecycle_events[-1]["reason"], "playwright_disconnect")

    def test_genuine_user_close_is_terminal_and_not_relaunched(self):
        disconnected = Mock()
        manager = BrowserManager(
            launch_url="https://chat.deepseek.com/",
            on_disconnect=disconnected,
        )
        manager._mark_disconnected("user_close")
        event = manager.lifecycle_events[-1]
        self.assertEqual(event["event_name"], "auth.interrupted")
        self.assertEqual(event["initiator"], "external")
        self.assertEqual(event["reason"], "user_close")
        disconnected.assert_called_once_with("user_close")

    def test_cleanup_close_during_handoff_is_not_user_close(self):
        manager = BrowserManager(launch_url="https://chat.deepseek.com/")
        manager._cleanup_in_progress = True
        manager._cleanup_reason = "handoff"
        manager._mark_disconnected("user_close")
        event = manager.lifecycle_events[-1]
        self.assertEqual(event["event_name"], "auth.cleanup")
        self.assertEqual(event["initiator"], "mimicgate_cleanup")
        self.assertEqual(event["reason"], "handoff")

    def test_display_launch_failure_remains_the_terminal_diagnostic(self):
        starter = Mock()
        starter.start = AsyncMock(side_effect=RuntimeError("no display"))
        manager = BrowserManager(profile_path=f"/tmp/mimicgate-display-{id(starter)}", headless=False)
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            with self.assertRaisesRegex(RuntimeError, "no display"):
                asyncio.run(manager.start())
        event = manager.lifecycle_events[-1]
        self.assertEqual(event["event_name"], "auth.interrupted")
        self.assertEqual(event["reason"], "display_session_failure")

    def test_manual_auth_wait_reports_login_interrupted(self):
        page = Mock()
        page.is_closed.return_value = False
        interruption = {"state": "LOGIN_INTERRUPTED", "reason": "user_close"}
        with self.assertRaisesRegex(RuntimeError, "LOGIN_INTERRUPTED"):
            asyncio.run(_wait_for_auth("deepseek", page, AUTH_TARGETS["deepseek"], interruption=interruption))

    def test_interrupted_service_never_starts_auth_watcher(self):
        service = DeepSeekService(load_config())
        service._set_auth_state("LOGIN_INTERRUPTED", "page_crash")
        service._start_auth_watcher(1)
        self.assertIsNone(service._auth_watch_task)
        with self.assertRaisesRegex(RuntimeError, "retry_login"):
            asyncio.run(service.start())

    def test_manual_auth_rejects_cdp_mode(self):
        config = {"browser": {"mode": "cdp"}}
        with self.assertRaisesRegex(RuntimeError, "browser.mode=managed"):
            asyncio.run(run_manual_auth("deepseek", config=config))

    def test_manual_auth_rejects_executable_mismatch_before_launch(self):
        config = {
            "browser": {"mode": "managed", "profile_dir": ".profile", "executable_path": "/configured/chrome"},
        }
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            asyncio.run(run_manual_auth("deepseek", executable_path="/other/chrome", config=config))

    def test_manual_auth_uses_provider_profile_and_configured_executable(self):
        from unittest.mock import patch
        from pathlib import Path

        page = AsyncMock()
        page.is_closed.return_value = False
        page.url = "https://chat.qwen.ai/"
        manager = Mock()
        manager.page = AsyncMock(return_value=page)
        manager.primary_page = AsyncMock(return_value=page)
        manager.handoff_to_headless = AsyncMock()
        manager.stop = AsyncMock()
        config = {
            "browser": {"mode": "managed", "profile_dir": ".profile", "executable_path": "./chrome"},
            "qwen": {"profile_dir": "./qwen-profile"},
        }
        with patch("mimicgate.manual_auth.BrowserManager", return_value=manager) as manager_class, patch(
            "mimicgate.manual_auth._probe_auth", new=AsyncMock(return_value="CHAT_READY")
        ):
            asyncio.run(run_manual_auth("qwen", config=config))
        kwargs = manager_class.call_args.kwargs
        self.assertEqual(kwargs["profile_path"], str(Path("./qwen-profile").resolve()))
        self.assertEqual(kwargs["executable_path"], str(Path("./chrome").resolve()))
        page.goto.assert_not_awaited()
    def test_deepseek_remote_delete_uses_web_ui_confirmation(self):
        page = Mock()
        page.url = "https://chat.deepseek.com/a/chat/s/abc123"
        link = Mock()
        link.count = AsyncMock(return_value=1)
        menu = Mock()
        menu.count = AsyncMock(return_value=1)
        menu.click = AsyncMock()
        link.locator.return_value.last = menu
        page.locator.return_value.last = link
        delete_item = Mock()
        delete_item.click = AsyncMock()
        confirm = Mock()
        confirm.click = AsyncMock()
        page.get_by_text.return_value.last = delete_item
        page.get_by_role.return_value = confirm
        page.wait_for_timeout = AsyncMock()

        deleted = asyncio.run(DeepSeekChat(page).delete_remote_conversation())

        self.assertTrue(deleted)
        page.locator.assert_called_once_with('a[href="/a/chat/s/abc123"]')
        menu.click.assert_awaited_once()
        delete_item.click.assert_awaited_once()
        confirm.click.assert_awaited_once()

    def test_deepseek_remote_delete_fails_closed_off_chat_url(self):
        page = Mock()
        page.url = "https://chat.deepseek.com/"
        deleted = asyncio.run(DeepSeekChat(page).delete_remote_conversation())
        self.assertFalse(deleted)
        page.locator.assert_not_called()

    def test_deepseek_remote_delete_fails_closed_without_current_chat_link(self):
        page = Mock()
        page.url = "https://chat.deepseek.com/a/chat/s/abc123"
        link = Mock()
        link.count = AsyncMock(return_value=0)
        page.locator.return_value.last = link
        deleted = asyncio.run(DeepSeekChat(page).delete_remote_conversation())
        self.assertFalse(deleted)

    def test_config_defaults_when_file_missing(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            cfg = load_config(Path(directory) / "missing.yaml")
        self.assertEqual(cfg["server"]["port"], 11555)
        self.assertEqual(cfg["browser"]["mode"], "managed")
        self.assertEqual(cfg["browser"]["restart_retries"], 1)
        self.assertEqual(cfg["browser"]["max_pages"], 8)
        self.assertEqual(cfg["browser"]["idle_timeout_ms"], 300000)
        self.assertEqual(cfg["deepseek"]["login_timeout_ms"], 30000)

    def test_config_accepts_local_chromium_cdp_endpoint(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("browser:\n  cdp_endpoint: http://127.0.0.1:9222\n")
            cfg = load_config(path)
        self.assertEqual(cfg["browser"]["cdp_endpoint"], "http://127.0.0.1:9222")
        self.assertEqual(cfg["browser"]["mode"], "cdp")

    def test_config_preserves_explicit_browser_mode(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("browser:\n  mode: managed\n  cdp_endpoint: http://127.0.0.1:9222\n")
            cfg = load_config(path)
        self.assertEqual(cfg["browser"]["mode"], "managed")

    def test_config_accepts_explicit_cdp_mode(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("browser:\n  mode: cdp\n  cdp_endpoint: http://127.0.0.1:9222\n")
            cfg = load_config(path)
        self.assertEqual(cfg["browser"]["mode"], "cdp")

    def test_find_free_port_skips_busy_port(self):
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            busy_port = sock.getsockname()[1]
            self.assertEqual(find_free_port("127.0.0.1", busy_port), busy_port + 1)

    def test_config_path_can_come_from_environment(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import os

        with TemporaryDirectory() as directory:
            path = Path(directory) / "custom.yaml"
            path.write_text("server:\n  port: 8123\n")
            old = os.environ.get("MIMICGATE_CONFIG")
            os.environ["MIMICGATE_CONFIG"] = str(path)
            try:
                self.assertEqual(load_config(None)["server"]["port"], 8123)
            finally:
                if old is None:
                    os.environ.pop("MIMICGATE_CONFIG", None)
                else:
                    os.environ["MIMICGATE_CONFIG"] = old

    def test_canonical_config_environment_is_used(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import os

        with TemporaryDirectory() as directory:
            canonical = Path(directory) / "canonical.yaml"
            canonical.write_text("server:\n  port: 8124\n")
            old_value = os.environ.get("MIMICGATE_CONFIG")
            os.environ["MIMICGATE_CONFIG"] = str(canonical)
            try:
                self.assertEqual(load_config(None)["server"]["port"], 8124)
            finally:
                if old_value is None:
                    os.environ.pop("MIMICGATE_CONFIG", None)
                else:
                    os.environ["MIMICGATE_CONFIG"] = old_value

    def test_config_allows_explicit_disabled_page_cleanup(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("browser:\n  max_pages:\n  idle_timeout_ms:\n")
            cfg = load_config(path)
        self.assertIsNone(cfg["browser"]["max_pages"])
        self.assertIsNone(cfg["browser"]["idle_timeout_ms"])

    def test_invalid_config_fails_fast(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("server:\n  port: 99999\n")
            with self.assertRaisesRegex(RuntimeError, "Invalid configuration"):
                load_config(path)

    def test_credential_store_round_trip_is_not_plaintext(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store_path = root / "credentials.json"
            key_path = root / "key"
            store = CredentialStore(store_path=store_path, key_path=key_path)
            store.save("user@example.com", "secret-password")

            self.assertEqual(store.load(), ("user@example.com", "secret-password"))
            raw = store_path.read_text()
            self.assertNotIn("secret-password", raw)
            self.assertNotIn("user@example.com", raw)
            self.assertEqual(json.loads(raw)["version"], 1)

    def test_redact_masks_secret_values(self):
        self.assertEqual(
            redact({"password": "secret", "safe": "ok"}),
            {"password": "[REDACTED]", "safe": "ok"},
        )
        self.assertNotIn("secret", redact("password=secret token:abc"))


    def test_prompt_adds_default_system_instruction(self):
        prompt = _prompt([Message(role="user", content="hello")], "Absolute mode. Short answer.")
        self.assertTrue(prompt.startswith("SYSTEM: Absolute mode. Short answer.\n\nUSER: hello"))

    def test_prompt_accepts_rich_openai_content(self):
        prompt = _prompt([Message(role="user", content=[{"type": "text", "text": "hello"}])])
        self.assertTrue(prompt.startswith("USER: hello"))

    def test_openclaw_title_requests_are_detected_locally(self):
        messages = [
            Message(role="system", content="Generate a concise session title (3-6 words)."),
            Message(role="user", content="What can you do?"),
        ]
        self.assertTrue(_is_title_request(messages))
        self.assertEqual(_local_title(messages), "What can you do?")

    def test_persian_prompt_preserves_unicode(self):
        self.assertTrue(_prompt([Message(role="user", content="به فارسی پاسخ بده")]).startswith("USER: به فارسی پاسخ بده"))

    def test_text_tool_marker_becomes_structured_call(self):
        call, visible = _extract_tool_call(
            '<tool_call>{"name":"exec","arguments":{"command":"printf {\\"ok\\":true}"}}</tool_call>',
            [{"type": "function", "function": {"name": "exec"}}],
        )
        self.assertEqual(visible, "")
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": 'printf {"ok":true}'})

    def test_unknown_text_tool_marker_is_not_executed(self):
        call, visible = _extract_tool_call(
            '<tool_call>{"name":"rm_everything","arguments":{}}</tool_call>',
            [{"type": "function", "function": {"name": "exec"}}],
        )
        self.assertIsNone(call)
        self.assertIn("rm_everything", visible)

    def test_markdown_json_tool_call_becomes_structured_call(self):
        answer = "json\nCopy\nDownload\n```json\n{\n  \"name\": \"exec\",\n  \"arguments\": {\"command\": \"openclaw status\"}\n}\n```"
        call, visible = _extract_tool_call(
            answer,
            [{"type": "function", "function": {"name": "exec"}}],
        )
        self.assertEqual(visible, "json\nCopy\nDownload")
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": "openclaw status"})

    def test_rendered_json_tool_call_without_code_fence(self):
        answer = 'json\nCopy\nDownload\n{\n  "name": "exec",\n  "arguments": {"command": "openclaw status"}\n}'
        call, visible = _extract_tool_call(
            answer,
            [{"type": "function", "function": {"name": "exec"}}],
        )
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": "openclaw status"})
        self.assertEqual(visible, "json\nCopy\nDownload")

    def test_renderer_json_block_is_restored_as_markdown_code(self):
        answer = 'json\nCopy\nDownload\n{\n  "plugins": {\n    "enable": ["example"]\n  }\n}'
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            '```json\n{\n  "plugins": {\n    "enable": ["example"]\n  }\n}\n```',
        )

    def test_renderer_html_block_ignores_run_label(self):
        answer = "html\nCopy\nDownload\nRun\n<!DOCTYPE html>\n<html><body>Hello</body></html>"
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "```html\n<!DOCTYPE html>\n<html><body>Hello</body></html>\n```",
        )

    def test_renderer_python_block_is_restored_as_markdown_code(self):
        answer = 'python\nCopy\nDownload\nprint("Hello World")'
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            '```python\nprint("Hello World")\n```',
        )

    def test_renderer_bash_block_is_restored_as_markdown_code(self):
        answer = "bash\nCopy\nDownload\nps aux | grep opencode\nkill 403927"
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "```bash\nps aux | grep opencode\nkill 403927\n```",
        )

    def test_renderer_bash_block_excludes_following_explanation(self):
        answer = "bash\nCopy\nDownload\necho hello\n\nاین فقط یک نمونه است و اجرا نمی‌شود."
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "```bash\necho hello\n```\nاین فقط یک نمونه است و اجرا نمی‌شود.",
        )

    def test_renderer_text_chart_is_restored_as_left_aligned_code_block(self):
        answer = "text\nCopy\nDownload\n  6.60 ┤   ▇\n  6.50 ┤ ▇ ▇\n       └────────\n         ش  ی  ن"
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "```text\n  6.60 ┤   ▇\n  6.50 ┤ ▇ ▇\n       └────────\n         ش  ی  ن\n```",
        )

    def test_renderer_text_chart_only_wraps_chart_not_following_explanation(self):
        answer = "text\nCopy\nDownload\nنمودار فروش\n  ۱۰ ┤ ▇▇\n   ۵ ┤ ▇\n     └────\n\nتوضیح: فروش هفته اول کمتر است."
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "```text\nنمودار فروش\n  ۱۰ ┤ ▇▇\n   ۵ ┤ ▇\n     └────\n```\nتوضیح: فروش هفته اول کمتر است.",
        )

    def test_renderer_blocks_for_multiple_languages_are_restored(self):
        examples = {
            "xml": "<?xml version=\"1.0\"?>\n<root />",
            "json": '{"ok": true}',
            "c#": "using System;\nConsole.WriteLine(\"Hello\");",
            "sql": "SELECT id FROM users;",
            "yaml": "name: mimicgate\nenabled: true",
            "javascript": "const answer = 42;",
            "bash": "#!/usr/bin/env bash\necho hello",
            "markdown": "# Hello\n\nText",
        }
        for language, body in examples.items():
            with self.subTest(language=language):
                answer = f"{language}\nCopy\nDownload\n{body}"
                self.assertEqual(_clean_renderer_artifacts(answer), f"```{language}\n{body}\n```")

    def test_multiple_renderer_blocks_in_one_answer_are_restored(self):
        answer = "```xml\n<hello>world</hello>\n```\njson\nCopy\nDownload\n{\"hello\":\"world\"}\ncsharp\nCopy\nDownload\nConsole.WriteLine(\"Hello World\");\nsql\nCopy\nDownload\nSELECT 1;"
        expected = "```xml\n<hello>world</hello>\n```\n```json\n{\"hello\":\"world\"}\n```\n```csharp\nConsole.WriteLine(\"Hello World\");\n```\n```sql\nSELECT 1;\n```"
        self.assertEqual(_clean_renderer_artifacts(answer), expected)

    def test_renderer_mermaid_block_is_restored_for_github_syntax(self):
        answer = "mermaid\nCopy\nDownload\ngraph TD;\n    A-->B;\n    A-->C;"
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "```mermaid\ngraph TD;\n    A-->B;\n    A-->C;\n```",
        )

    def test_renderer_mermaid_supports_common_diagram_types(self):
        for source in ("flowchart LR\n  A-->B", "sequenceDiagram\n  Alice->>Bob: Hello", "pie\n  \"A\" : 1", "xychart-beta\n  bar [10, 20]"):
            with self.subTest(source=source):
                language = "mermaid\nCopy\nDownload\n" + source
                self.assertEqual(_clean_renderer_artifacts(language), f"```mermaid\n{source}\n```")

    def test_renderer_mermaid_toolbar_with_source_is_restored(self):
        answer = "Diagram\nCode\nCopy\nDownload\nFullscreen\ngraph TD;\n  A-->B;"
        self.assertEqual(_clean_renderer_artifacts(answer), "```mermaid\ngraph TD;\n  A-->B;\n```")

    def test_renderer_mermaid_toolbar_supports_xy_chart_source(self):
        answer = "قبل از نمودار\n\nDiagram\nCode\nCopy\nDownload\nFullscreen\nxychart-beta\n  title \"Sales\"\n  bar [10, 20, 15]\n\nتوضیح خارج از نمودار"
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            "قبل از نمودار\n\n```mermaid\nxychart-beta\n  title \"Sales\"\n  bar [10, 20, 15]\n```\n\nتوضیح خارج از نمودار",
        )

    def test_renderer_labels_without_code_are_left_alone(self):
        answer = "sql\nCopy\nDownload\nThe query is ready."
        self.assertEqual(_clean_renderer_artifacts(answer), answer)

    def test_renderer_svg_block_is_restored_and_trailing_text_stays_outside(self):
        answer = 'svg\nCopy\nDownload\nRun\n<svg viewBox="0 0 10 10"><rect width="10" height="10" /></svg> نمودار شش هفته‌ای.'
        self.assertEqual(
            _clean_renderer_artifacts(answer),
            '```svg\n<svg viewBox="0 0 10 10"><rect width="10" height="10" /></svg>\n```\nنمودار شش هفته‌ای.',
        )

    def test_malformed_quoted_arguments_tool_call_is_recovered(self):
        answer = '<tool_call>{"name":"exec","arguments":"{"command":"mkdir -p helloIran","yieldMs":5000}"}</tool_call>'
        call, visible = _extract_tool_call(
            answer,
            [{"type": "function", "function": {"name": "exec"}}],
        )
        self.assertEqual(visible, "")
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": "mkdir -p helloIran", "yieldMs": 5000})

    def test_malformed_write_content_with_html_quotes_is_recovered(self):
        answer = '<tool_call>{"name":"write","arguments":{"path":"hello-iran/index.html","content":"<html lang="en"><title>Hello Iran</title></html>"}}</tool_call>'
        call, _ = _extract_tool_call(
            answer,
            [{"type": "function", "function": {"name": "write"}}],
        )
        self.assertEqual(call["function"]["name"], "write")
        self.assertEqual(json.loads(call["function"]["arguments"]), {
            "path": "hello-iran/index.html",
            "content": '<html lang="en"><title>Hello Iran</title></html>',
        })

    def test_fallback_conversation_id_is_stable(self):
        first = [Message(role="user", content="Hello"), Message(role="assistant", content="Hi")]
        continued = first + [Message(role="user", content="Continue")]
        self.assertEqual(_fallback_conversation_id(first), _fallback_conversation_id(continued))
        self.assertNotEqual(_fallback_conversation_id(first), _fallback_conversation_id([Message(role="user", content="Different")]))

    def test_provider_router_dispatches_qwen_model(self):
        router = ProviderRouter({"deepseek": FakeProvider(), "qwen": FakeProvider()}, "deepseek")
        router.providers["qwen"].name = "qwen"
        self.assertEqual(router.provider_for_model("qwen-chat").name, "qwen")

    def test_manual_auth_targets_include_qwen_google(self):
        target = AUTH_TARGETS["qwen"]
        self.assertEqual(target.url, "https://chat.qwen.ai/")
        self.assertTrue(any("Google" in selector for selector in target.google_selectors))

    def test_provider_router_dispatches_prefixed_model(self):
        router = ProviderRouter({"fake": FakeProvider()}, "fake")
        self.assertEqual(router.provider_for_model("fake:any").name, "fake")


class PageCapacityTests(unittest.IsolatedAsyncioTestCase):
    def _deepseek(self, max_pages=8, idle_timeout_ms=300000):
        from mimicgate.service import DeepSeekService

        config = load_config('/nonexistent')
        config["browser"]["max_pages"] = max_pages
        config["browser"]["idle_timeout_ms"] = idle_timeout_ms
        service = DeepSeekService(config)
        service.browser = Mock()
        service.browser.release_page = Mock()
        service.browser.page_for = AsyncMock()
        return service

    async def test_idle_cleanup_deduplicates_aliases_and_is_local_only(self):
        service = self._deepseek(max_pages=1, idle_timeout_ms=0)
        page = Mock()
        page.is_closed.return_value = False
        page.close = AsyncMock()
        service._conversation_pages.update({"conversation": page, "session-key": page})
        service._track_page(page)
        self.assertEqual(await service.cleanup_pages(), 1)
        self.assertEqual(service._conversation_pages, {})
        page.close.assert_awaited_once()
        service.browser.release_page.assert_called_once_with(page)

    async def test_active_page_blocks_capacity_eviction(self):
        service = self._deepseek(max_pages=1, idle_timeout_ms=0)
        page = Mock()
        page.is_closed.return_value = False
        service._conversation_pages["active"] = page
        service._track_page(page)
        service._mark_page_active(page)
        with self.assertRaisesRegex(RuntimeError, "capacity reached"):
            await service._page_for_conversation("new")
        service.browser.page_for.assert_not_awaited()

    async def test_recent_page_is_retained_before_idle_threshold(self):
        service = self._deepseek(max_pages=1, idle_timeout_ms=300000)
        page = Mock()
        page.is_closed.return_value = False
        page.close = AsyncMock()
        service._conversation_pages["recent"] = page
        service._track_page(page)
        self.assertEqual(await service.cleanup_pages(), 0)
        page.close.assert_not_awaited()

    async def test_default_page_is_counted_by_cap_tracking(self):
        service = self._deepseek(max_pages=1)
        page = Mock()
        page.is_closed.return_value = False
        service.browser.page_for = AsyncMock(return_value=page)
        self.assertIs(await service._page_for_conversation(None), page)
        self.assertEqual(len(service._page_records), 1)

    async def test_concurrent_cleanup_does_not_close_active_page(self):
        service = self._deepseek(max_pages=1, idle_timeout_ms=0)
        page = Mock()
        page.is_closed.return_value = False
        page.close = AsyncMock()
        service._conversation_pages["active"] = page
        service._track_page(page)
        service._mark_page_active(page)
        await asyncio.gather(service.cleanup_pages(), service.cleanup_pages())
        page.close.assert_not_awaited()
        self.assertIn(id(page), service._page_records)


class BrowserManagerTests(unittest.IsolatedAsyncioTestCase):
    def _managed_playwright(self, contexts):
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(side_effect=contexts)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)
        return starter, chromium, playwright

    async def test_managed_disconnect_marks_manager_dead_and_recovers_once(self):
        first = Mock(pages=[])
        first.browser = None
        first.close = AsyncMock()
        second = Mock(pages=[])
        second.browser = None
        second.close = AsyncMock()
        starter, chromium, _ = self._managed_playwright([first, second])

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=f"/tmp/mimicgate-test-{id(first)}")
            self.assertIs(await manager.start(), first)
            self.assertTrue(manager.is_running)
            context_callback = first.on.call_args.args[1]
            context_callback()
            self.assertFalse(manager.is_running)
            self.assertIs(await manager.start(), second)
            self.assertEqual(chromium.launch_persistent_context.await_count, 2)
            await manager.stop()

    async def test_concurrent_starts_share_one_context_and_lifecycle(self):
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock()
        starter, chromium, _ = self._managed_playwright([context])
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=f"/tmp/mimicgate-concurrent-{id(context)}")
            first, second = await asyncio.gather(manager.start(), manager.start())
            self.assertIs(first, context)
            self.assertIs(second, context)
            self.assertEqual(chromium.launch_persistent_context.await_count, 1)
            self.assertEqual(manager.lifecycle_state, "RUNNING")
            await asyncio.gather(manager.stop(), manager.stop())
            self.assertEqual(manager.lifecycle_state, "STOPPED")

    async def test_restart_is_serialized_with_start(self):
        first = Mock(pages=[])
        first.browser = None
        first.close = AsyncMock()
        second = Mock(pages=[])
        second.browser = None
        second.close = AsyncMock()
        starter, chromium, _ = self._managed_playwright([first, second])
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=f"/tmp/mimicgate-restart-{id(first)}")
            await manager.start()
            result, observed = await asyncio.gather(manager.restart(), manager.start())
            self.assertIs(result, second)
            self.assertIs(observed, second)
            self.assertEqual(chromium.launch_persistent_context.await_count, 2)
            await manager.stop()

    async def test_cancellation_during_launch_releases_state_and_lock(self):
        chromium = Mock()
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock(start=AsyncMock(side_effect=asyncio.CancelledError()))
        profile = "/tmp/mimicgate-cancel-launch-test"
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile)
            with self.assertRaises(asyncio.CancelledError):
                await manager.start()
            self.assertFalse(manager.is_running)
            self.assertEqual(manager.lifecycle_state, "STOPPED")
            self.assertIsNone(manager._lock_fd)

    async def test_cancellation_during_stop_finishes_cleanup(self):
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock(side_effect=asyncio.CancelledError())
        starter, _, playwright = self._managed_playwright([context])
        profile = f"/tmp/mimicgate-cancel-stop-{id(context)}"
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile)
            await manager.start()
            with self.assertRaises(asyncio.CancelledError):
                await manager.stop()
            self.assertFalse(manager.is_running)
            self.assertEqual(manager.lifecycle_state, "STOPPED")
            self.assertIsNone(manager._lock_fd)
            playwright.stop.assert_awaited_once()

    async def test_healthy_context_is_reused_without_relaunch(self):
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock()
        starter, chromium, _ = self._managed_playwright([context])

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=f"/tmp/mimicgate-test-{id(context)}")
            self.assertIs(await manager.start(), context)
            self.assertIs(await manager.start(), context)
            chromium.launch_persistent_context.assert_awaited_once()
            await manager.stop()

    async def test_managed_recovery_failure_clears_state(self):
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock()
        starter, chromium, playwright = self._managed_playwright([context])
        chromium.launch_persistent_context.side_effect = [context, RuntimeError("launch failed")]

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=f"/tmp/mimicgate-test-{id(context)}")
            await manager.start()
            context.on.call_args.args[1]()
            with self.assertRaisesRegex(RuntimeError, "launch failed"):
                await manager.start()
            self.assertFalse(manager.is_running)
            self.assertIsNone(manager.context)
            self.assertIsNone(manager.playwright)
            playwright.stop.assert_awaited()

    async def test_headed_handoff_reuses_profile_and_executable_headlessly(self):
        headed = Mock(pages=[])
        headed.browser = None
        headed.close = AsyncMock()
        headless = Mock(pages=[])
        headless.browser = None
        headless.close = AsyncMock()
        headless.pages = [Mock(is_closed=Mock(return_value=False))]
        headed_chromium = Mock()
        headed_chromium.launch_persistent_context = AsyncMock(return_value=headed)
        headless_chromium = Mock()
        headless_chromium.launch_persistent_context = AsyncMock(return_value=headless)
        headed_playwright = Mock(chromium=headed_chromium)
        headed_playwright.stop = AsyncMock()
        headless_playwright = Mock(chromium=headless_chromium)
        headless_playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(side_effect=[headed_playwright, headless_playwright])
        executable = "/usr/bin/chromium-test"
        profile = "/tmp/mimicgate-handoff-test"

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile, executable_path=executable, headless=False)
            await manager.start()
            auth_probe = AsyncMock(return_value=True)
            result = await manager.handoff_to_headless(auth_probe=auth_probe)
            self.assertIs(result, headless)
            self.assertTrue(manager.headless)
            auth_probe.assert_awaited_once()
            handoff_events = [event for event in manager.lifecycle_events if event["reason"] == "handoff"]
            self.assertTrue(handoff_events)
            self.assertTrue(all(event["initiator"] == "mimicgate_cleanup" for event in handoff_events))
            self.assertFalse(any(event["reason"] == "user_close" for event in manager.lifecycle_events))

        headed_chromium.launch_persistent_context.assert_awaited_once_with(
            user_data_dir=profile,
            headless=False,
            executable_path=executable,
            viewport={"width": 1440, "height": 1000},
            args=[
                "--app=about:blank",
                "--disable-sync",
                "--disable-default-apps",
                "--disable-extensions",
                "--no-first-run",
            ],
        )
        headless_chromium.launch_persistent_context.assert_awaited_once_with(
            user_data_dir=profile,
            headless=True,
            executable_path=executable,
            viewport={"width": 1440, "height": 1000},
        )
        headed.close.assert_awaited_once()
        headed_playwright.stop.assert_awaited_once()
        await manager.stop()

    async def test_handoff_opens_provider_url_when_headless_context_starts_blank(self):
        headed = Mock(pages=[])
        headed.browser = None
        headed.close = AsyncMock()
        headless_page = Mock(url="about:blank", is_closed=Mock(return_value=False))
        headless_page.goto = AsyncMock()
        headless = Mock(pages=[headless_page])
        headless.browser = None
        headless.close = AsyncMock()
        starter = Mock()
        headed_pw = Mock(chromium=Mock(launch_persistent_context=AsyncMock(return_value=headed)))
        headless_pw = Mock(chromium=Mock(launch_persistent_context=AsyncMock(return_value=headless)))
        headed_pw.stop = AsyncMock()
        headless_pw.stop = AsyncMock()
        starter.start = AsyncMock(side_effect=[headed_pw, headless_pw])

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(
                profile_path=f"/tmp/mimicgate-handoff-blank-{id(headed)}",
                launch_url="https://chat.deepseek.com/",
                headless=False,
            )
            await manager.start()
            await manager.handoff_to_headless(auth_probe=AsyncMock(return_value=True))
            headless_page.goto.assert_awaited_once_with(
                "https://chat.deepseek.com/", wait_until="domcontentloaded"
            )
            await manager.stop()

    async def test_headed_login_uses_one_app_page_and_headless_has_no_app_args(self):
        page = Mock(url="about:blank")
        page.is_closed.return_value = False
        context = Mock(pages=[page], browser=None)
        context.close = AsyncMock()
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(return_value=context)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock(start=AsyncMock(return_value=playwright))

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(
                profile_path=f"/tmp/mimicgate-app-{id(context)}",
                headless=False,
                launch_url="https://chat.deepseek.com/",
            )
            self.assertIs(await manager.primary_page("deepseek"), page)
            self.assertEqual(len(context.pages), 1)
            await manager.stop()
        kwargs = chromium.launch_persistent_context.await_args.kwargs
        self.assertIn("--app=https://chat.deepseek.com/", kwargs["args"])
        self.assertIn("--disable-sync", kwargs["args"])

        headless_context = Mock(pages=[], browser=None)
        headless_context.close = AsyncMock()
        headless_chromium = Mock()
        headless_chromium.launch_persistent_context = AsyncMock(return_value=headless_context)
        headless_playwright = Mock(chromium=headless_chromium)
        headless_playwright.stop = AsyncMock()
        headless_starter = Mock(start=AsyncMock(return_value=headless_playwright))
        with patch("mimicgate.browser.manager.async_playwright", return_value=headless_starter):
            manager = BrowserManager(profile_path=f"/tmp/mimicgate-headless-{id(context)}", headless=True)
            await manager.start()
            await manager.stop()
        self.assertNotIn("args", headless_chromium.launch_persistent_context.await_args.kwargs)

    async def test_handoff_auth_probe_failure_restores_headed_session(self):
        headed = Mock(pages=[])
        headed.browser = None
        headed.close = AsyncMock()
        headless = Mock(pages=[])
        headless.browser = None
        headless.close = AsyncMock()
        restored = Mock(pages=[])
        restored.browser = None
        restored.close = AsyncMock()
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(side_effect=[headed, headless, restored])
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)
        profile = "/tmp/mimicgate-handoff-auth-failure-test"

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile, headless=False)
            await manager.start()
            with self.assertRaisesRegex(RuntimeError, "headed session was restored"):
                await manager.handoff_to_headless(auth_probe=AsyncMock(return_value=False))
            self.assertFalse(manager.headless)
            self.assertIs(manager.context, restored)
            self.assertTrue(manager.is_running)
            await manager.stop()

    async def test_handoff_launch_failure_restores_headed_session(self):
        headed = Mock(pages=[])
        headed.browser = None
        headed.close = AsyncMock()
        restored = Mock(pages=[])
        restored.browser = None
        restored.close = AsyncMock()
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(
            side_effect=[headed, RuntimeError("headless launch failed"), restored]
        )
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)
        profile = "/tmp/mimicgate-handoff-launch-failure-test"

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile, headless=False)
            await manager.start()
            with self.assertRaisesRegex(RuntimeError, "headed session was restored"):
                await manager.handoff_to_headless()
            self.assertFalse(manager.headless)
            self.assertIs(manager.context, restored)
            await manager.stop()

    async def test_managed_profile_lock_conflict_blocks_second_manager(self):
        profile = "/tmp/mimicgate-lock-conflict-test"
        first_context = Mock(pages=[])
        first_context.browser = None
        first_context.close = AsyncMock()
        starter, _, _ = self._managed_playwright([first_context])
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            first = BrowserManager(profile_path=profile)
            second = BrowserManager(profile_path=profile)
            await first.start()
            with self.assertRaisesRegex(RuntimeError, "profile is locked"):
                await second.start()
            self.assertIsNone(second.playwright)
            await first.stop()

    async def test_profile_lock_metadata_is_diagnostic_and_removed_by_owner(self):
        profile = "/tmp/mimicgate-lock-metadata-test"
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock()
        starter, _, _ = self._managed_playwright([context])
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile, executable_path="/usr/bin/chromium")
            await manager.start()
            metadata_path = manager.profile_path / ".mimicgate-profile.lock.json"
            metadata = json.loads(metadata_path.read_text())
            self.assertEqual(metadata["owner_pid"], os.getpid())
            self.assertEqual(metadata["profile"], str(manager.profile_path))
            self.assertEqual(metadata["executable"], "/usr/bin/chromium")
            self.assertEqual(metadata["mode"], "headed")
            await manager.stop()
            self.assertFalse(metadata_path.exists())

    async def test_stale_profile_metadata_is_replaced_without_singleton_deletion(self):
        profile = "/tmp/mimicgate-stale-metadata-test"
        manager = BrowserManager(profile_path=profile)
        manager.profile_path.mkdir(parents=True, exist_ok=True)
        metadata_path = manager.profile_path / ".mimicgate-profile.lock.json"
        metadata_path.write_text(json.dumps({"owner_pid": 1, "profile": "/old/profile"}))
        singleton = manager.profile_path / "SingletonLock"
        singleton.write_text("preserve")
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock()
        starter, _, _ = self._managed_playwright([context])
        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            await manager.start()
            self.assertEqual(json.loads(metadata_path.read_text())["profile"], str(manager.profile_path))
            await manager.stop()
        self.assertTrue(singleton.exists())

    async def test_handoff_cancellation_releases_profile_lock(self):
        context = Mock(pages=[])
        context.browser = None
        context.close = AsyncMock()
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(
            side_effect=[context, asyncio.CancelledError(), context, context]
        )
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)
        profile = "/tmp/mimicgate-cancel-handoff-test"

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile, headless=False)
            await manager.start()
            with self.assertRaises(asyncio.CancelledError):
                await manager.handoff_to_headless()
            self.assertTrue(manager.is_running)
            await manager.stop()
            contender = BrowserManager(profile_path=profile)
            await contender.start()
            await contender.stop()

    async def test_cdp_mode_attaches_without_closing_user_chromium(self):
        context = Mock()
        context.close = AsyncMock()
        browser = Mock(contexts=[context])
        chromium = Mock()
        chromium.connect_over_cdp = AsyncMock(return_value=browser)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(cdp_endpoint="http://127.0.0.1:9222")
            self.assertIs(await manager.start(), context)
            chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222")
            await manager.stop()

        context.close.assert_not_awaited()
        playwright.stop.assert_awaited_once()

    async def test_cdp_disconnect_recovers_without_closing_user_chromium(self):
        first_context = Mock(pages=[])
        first_browser = Mock(contexts=[first_context])
        first_context.browser = first_browser
        first_context.close = AsyncMock()
        second_context = Mock(pages=[])
        second_browser = Mock(contexts=[second_context])
        second_context.browser = second_browser
        chromium = Mock()
        chromium.connect_over_cdp = AsyncMock(side_effect=[first_browser, second_browser])
        first_playwright = Mock(chromium=chromium)
        first_playwright.stop = AsyncMock()
        second_playwright = Mock(chromium=chromium)
        second_playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(side_effect=[first_playwright, second_playwright])

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(cdp_endpoint="http://127.0.0.1:9222")
            self.assertIs(await manager.start(), first_context)
            disconnect_callback = first_browser.on.call_args.args[1]
            disconnect_callback()
            self.assertFalse(manager.is_running)
            self.assertIs(await manager.start(), second_context)

        first_context.close.assert_not_awaited()
        first_playwright.stop.assert_awaited_once()

    async def test_cdp_page_selection_is_provider_aware_and_order_independent(self):
        unrelated = Mock(url="https://example.com", pages=[])
        deepseek_page = Mock(url="https://chat.deepseek.com/", pages=[])
        qwen_page = Mock(url="https://chat.qwen.ai/", pages=[])
        for page in (unrelated, deepseek_page, qwen_page):
            page.is_closed.return_value = False
        context = Mock(pages=[unrelated, qwen_page, deepseek_page])
        browser = Mock(contexts=[context])
        chromium = Mock()
        chromium.connect_over_cdp = AsyncMock(return_value=browser)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock(start=AsyncMock(return_value=playwright))

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(cdp_endpoint="http://127.0.0.1:9222")
            self.assertIs(await manager.page_for("deepseek", "d1"), deepseek_page)
            self.assertIs(await manager.page_for("qwen", "q1"), qwen_page)
            await manager.stop()

    async def test_page_claim_isolated_and_released_on_close(self):
        deepseek_page = Mock(url="https://chat.deepseek.com/")
        deepseek_page.is_closed.return_value = False
        qwen_replacement = Mock(url="about:blank")
        qwen_replacement.is_closed.return_value = False
        deepseek_replacement = Mock(url="about:blank")
        deepseek_replacement.is_closed.return_value = False
        context = Mock(pages=[deepseek_page])
        context.new_page = AsyncMock(side_effect=[qwen_replacement, deepseek_replacement])
        browser = Mock(contexts=[context])
        chromium = Mock()
        chromium.connect_over_cdp = AsyncMock(return_value=browser)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock(start=AsyncMock(return_value=playwright))

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(cdp_endpoint="http://127.0.0.1:9222")
            self.assertIs(await manager.page_for("deepseek", "d1"), deepseek_page)
            self.assertIs(await manager.page_for("qwen", "q1"), qwen_replacement)
            deepseek_close = deepseek_page.on.call_args.args[1]
            deepseek_page.is_closed.return_value = True
            deepseek_close()
            self.assertIs(await manager.page_for("deepseek", "d2"), deepseek_replacement)
            await manager.stop()

        deepseek_page.close.assert_not_called()

    async def test_managed_page_selection_skips_unrelated_tab(self):
        unrelated = Mock(url="https://example.com")
        unrelated.is_closed.return_value = False
        owned = Mock(url="https://chat.deepseek.com/")
        owned.is_closed.return_value = False
        context = Mock(pages=[unrelated, owned])
        context.browser = None
        context.close = AsyncMock()
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(return_value=context)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock(start=AsyncMock(return_value=playwright))
        profile = f"/tmp/mimicgate-managed-page-{id(context)}"

        with patch("mimicgate.browser.manager.async_playwright", return_value=starter):
            manager = BrowserManager(profile_path=profile)
            self.assertIs(await manager.page_for("deepseek", "d1"), owned)
            await manager.stop()



if __name__ == "__main__":
    unittest.main()
