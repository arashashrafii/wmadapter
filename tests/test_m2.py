from __future__ import annotations

import json
import unittest

from webbridgefreeride.config import load_config
from webbridgefreeride.credentials import CredentialStore
from webbridgefreeride.security import redact
from webbridgefreeride.providers.router import ProviderRouter
from webbridgefreeride.main import Message, _extract_tool_call, _fallback_conversation_id, _is_title_request, _local_title, _prompt
from webbridgefreeride.ports import find_free_port
from webbridgefreeride.manual_auth import AUTH_TARGETS
from webbridgefreeride.providers.base import ChatProvider


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
    def test_config_defaults_when_file_missing(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            cfg = load_config(Path(directory) / "missing.yaml")
        self.assertEqual(cfg["server"]["port"], 11555)
        self.assertEqual(cfg["browser"]["restart_retries"], 1)
        self.assertEqual(cfg["deepseek"]["login_timeout_ms"], 30000)

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
            old = os.environ.get("WEBBRIDGE_CONFIG")
            os.environ["WEBBRIDGE_CONFIG"] = str(path)
            try:
                self.assertEqual(load_config(None)["server"]["port"], 8123)
            finally:
                if old is None:
                    os.environ.pop("WEBBRIDGE_CONFIG", None)
                else:
                    os.environ["WEBBRIDGE_CONFIG"] = old

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
        self.assertEqual(prompt, "SYSTEM: Absolute mode. Short answer.\n\nUSER: hello")

    def test_prompt_accepts_rich_openai_content(self):
        prompt = _prompt([Message(role="user", content=[{"type": "text", "text": "hello"}])])
        self.assertEqual(prompt, "USER: hello")

    def test_openclaw_title_requests_are_detected_locally(self):
        messages = [
            Message(role="system", content="Generate a concise session title (3-6 words)."),
            Message(role="user", content="What can you do?"),
        ]
        self.assertTrue(_is_title_request(messages))
        self.assertEqual(_local_title(messages), "What can you do?")

    def test_persian_prompt_preserves_unicode(self):
        self.assertEqual(_prompt([Message(role="user", content="به فارسی پاسخ بده")]), "USER: به فارسی پاسخ بده")

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



if __name__ == "__main__":
    unittest.main()
