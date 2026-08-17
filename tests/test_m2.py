from __future__ import annotations

import json
import unittest

from webbridgefreeride.config import load_config
from webbridgefreeride.credentials import CredentialStore
from webbridgefreeride.security import redact
from webbridgefreeride.providers.router import ProviderRouter
from webbridgefreeride.main import Message, _prompt
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

    def test_manual_auth_targets_include_qwen_google(self):
        target = AUTH_TARGETS["qwen"]
        self.assertEqual(target.url, "https://chat.qwen.ai/")
        self.assertTrue(any("Google" in selector for selector in target.google_selectors))

    def test_provider_router_dispatches_prefixed_model(self):
        router = ProviderRouter({"fake": FakeProvider()}, "fake")
        self.assertEqual(router.provider_for_model("fake:any").name, "fake")


if __name__ == "__main__":
    unittest.main()
