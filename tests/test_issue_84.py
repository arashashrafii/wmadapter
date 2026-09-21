from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.__main__ import main as cli_main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import ModelCapabilities
from wmadapter.providers.router import ProviderRouter
from wmadapter.registry import discover_models


class FakeProvider(ChatProvider):
    def __init__(self, name: str, models: tuple[str, ...], ready: bool = True,
                 image_generation: bool = False):
        self.name = name
        self.model_ids = models
        self.capabilities = ModelCapabilities(image_generation=image_generation)
        self.ready = ready
        self.last_error = None

    async def start(self):
        self.ready = True

    async def stop(self):
        self.ready = False

    async def status(self):
        return {"provider": self.name, "ready": self.ready}

    async def complete(self, prompt, conversation_id=None):
        return prompt


class Issue84Tests(unittest.TestCase):
    def test_discovery_separates_chat_and_image_capabilities(self):
        config = {"qwen": {"models": ["qwen-chat"], "image_generation_verified": True}}
        records = discover_models(config, "qwen")
        self.assertEqual(records[0].model, "qwen-chat")
        self.assertEqual(records[0].capabilities, {"chat": True, "image_generation": True})

    def test_routing_is_deterministic_and_rejects_ambiguous_plain_names(self):
        first = FakeProvider("first", ("shared",))
        second = FakeProvider("second", ("shared",))
        router = ProviderRouter({"first": first, "second": second}, "first", ["first", "second"])
        with self.assertRaisesRegex(RuntimeError, "Ambiguous model"):
            router.resolve_model("shared")
        self.assertIs(router.resolve_model("second:shared"), second)

    def test_http_provider_prefix_is_preserved(self):
        deepseek = FakeProvider("deepseek", ("shared",))
        qwen = FakeProvider("qwen", ("shared",))
        router = ProviderRouter({"deepseek": deepseek, "qwen": qwen}, "deepseek",
                                ["deepseek", "qwen"])
        with patch.object(main, "router", router):
            self.assertIs(main._model_provider("qwen/shared"), qwen)

    def test_startup_failure_does_not_skip_healthy_provider(self):
        broken = FakeProvider("deepseek", ("deepseek-chat",))
        healthy = FakeProvider("qwen", ("qwen-chat",))
        broken.start = AsyncMock(side_effect=RuntimeError("login required"))
        healthy.start = AsyncMock()
        router = ProviderRouter({"deepseek": broken, "qwen": healthy}, "deepseek",
                                ["deepseek", "qwen"])
        with self.assertRaises(RuntimeError):
            asyncio.run(router.start())
        healthy.start.assert_awaited_once()
        self.assertTrue(healthy.ready)

    def test_models_endpoint_reports_provider_capability_readiness_and_default(self):
        deepseek = FakeProvider("deepseek", ("deepseek-chat",), ready=True)
        qwen = FakeProvider("qwen", ("qwen-chat",), ready=False, image_generation=True)
        qwen.start = AsyncMock()
        router = ProviderRouter({"deepseek": deepseek, "qwen": qwen}, "deepseek",
                                ["deepseek", "qwen"])
        with patch.object(main, "router", router):
            with TestClient(main.app) as client:
                data = client.get("/v1/models").json()["data"]
        self.assertEqual([(row["provider"], row["model"]) for row in data],
                         [("deepseek", "deepseek-chat"), ("qwen", "qwen-chat")])
        self.assertTrue(data[0]["default"])
        self.assertFalse(data[1]["readiness"])
        self.assertEqual(data[1]["capability"], {"chat": True, "image_generation": True})

    def test_add_provider_is_idempotent_and_preserves_manual_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump({
                "qwen": {"auth": "manual", "models": ["qwen-chat"]},
                "providers": {"default": "deepseek", "enabled": ["deepseek"],
                              "enabled_models": ["deepseek-chat"]},
            }))
            for _ in range(2):
                with patch.object(sys, "argv", ["wmadapter", "add-provider", "qwen", "--config", str(path)]):
                    with redirect_stdout(io.StringIO()):
                        cli_main()
            result = yaml.safe_load(path.read_text())
            self.assertEqual(result["qwen"]["auth"], "manual")
            self.assertEqual(result["providers"]["enabled"], ["deepseek", "qwen"])
            self.assertEqual(result["providers"]["enabled_models"], ["deepseek-chat", "qwen-chat"])

    def test_list_models_reports_discovered_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump({
                "qwen": {"image_generation_verified": True},
                "providers": {"default": "deepseek", "enabled": ["deepseek", "qwen"]},
            }))
            output = io.StringIO()
            with patch.object(sys, "argv", ["wmadapter", "list-models", "--config", str(path)]):
                with redirect_stdout(output):
                    cli_main()
            records = json.loads(output.getvalue())
            self.assertEqual(records[-1]["provider"], "qwen")
            self.assertTrue(records[-1]["capability"]["image_generation"])


if __name__ == "__main__":
    unittest.main()
