import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import ModelCapabilities
from wmadapter.providers.router import ProviderRouter


class LimitProvider(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat",)
    capabilities = ModelCapabilities(context_window=None, max_output_tokens=None)

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {"ready": True}

    async def complete(self, prompt, conversation_id=None):
        return "ok"


class GatewayLimitTests(unittest.TestCase):
    def setUp(self):
        self.provider = LimitProvider()
        self.provider.infer = AsyncMock(return_value=type("Result", (), {
            "content": "short", "tool_calls": [], "finish_reason": "stop", "usage": None,
        })())
        self.router_patch = patch.object(main, "router", ProviderRouter({"deepseek": self.provider}, "deepseek"))
        self.router_patch.start()
        self.limits = main.config["limits"]
        self.original_limits = dict(self.limits)
        self.addCleanup(self._restore_limits)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def _restore_limits(self):
        main.config["limits"].clear()
        main.config["limits"].update(self.original_limits)
        self.router_patch.stop()

    def post(self, **overrides):
        body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hello"}]}
        body.update(overrides)
        return self.client.post("/v1/chat/completions", json=body)

    def test_configured_input_limit_rejects_without_truncation(self):
        self.limits["max_input_chars"] = 4
        response = self.post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("input character limit", response.text)
        self.provider.infer.assert_not_called()

    def test_configured_output_limit_rejects_without_truncation(self):
        self.limits["max_output_chars"] = 3
        self.provider.infer = AsyncMock(return_value=type("Result", (), {
            "content": "long", "tool_calls": [], "finish_reason": "stop", "usage": None,
        })())
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertIn("gateway_output_limit", response.text)
        self.assertNotIn('"content":"lon"', response.text)

    def test_capabilities_advertise_gateway_limits_and_unknown_upstream_tokens(self):
        self.limits.update(max_input_chars=100, max_output_chars=50)
        body = self.client.get("/v1/models").json()["data"][0]
        self.assertEqual(body["capabilities"]["gateway_max_input_chars"], 100)
        self.assertEqual(body["capabilities"]["gateway_max_output_chars"], 50)
        self.assertIsNone(body["capabilities"]["context_window"])
        self.assertIsNone(body["capabilities"]["max_output_tokens"])

    def test_unconfigured_limits_do_not_change_existing_behavior(self):
        self.limits.update(max_input_chars=None, max_output_chars=None)
        response = self.post(messages=[{"role": "user", "content": "x" * 1000}])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "short")


if __name__ == "__main__":
    unittest.main()
