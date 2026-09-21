import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.opencode import translate_request
from wmadapter.providers.router import ProviderRouter
from wmadapter.providers.policy import ClientPolicy


class OpenCodeProvider(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat",)

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {"ready": True}

    async def complete(self, prompt, conversation_id=None):
        return "hello"


class OpenCodeChannelTests(unittest.TestCase):
    def setUp(self):
        self.provider = OpenCodeProvider()
        self.provider.infer = AsyncMock(return_value=type("Result", (), {
            "content": "hello", "tool_calls": [], "finish_reason": "stop", "usage": None,
        })())
        self.router_patch = patch.object(main, "router", ProviderRouter({"deepseek": self.provider}, "deepseek"))
        self.router_patch.start()
        self.addCleanup(self.router_patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def request(self, **overrides):
        body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hello"}]}
        body.update(overrides)
        return body

    def test_opencode_channel_returns_shared_safe_completion_shape(self):
        response = self.client.post("/v1/opencode/chat/completions", json=self.request())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello")
        self.assertEqual(self.provider.infer.call_args.args[0].chat.messages[0].content, "hello")

    def test_opencode_translation_preserves_tools_and_explicit_conversation(self):
        payload = self.request(
            model="opencode/deepseek-chat",
            conversation_id="opencode-session",
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        )
        translated = translate_request(payload)

        self.assertEqual(translated.model, "opencode/deepseek-chat")
        self.assertEqual(translated.conversation_id, "opencode-session")
        self.assertEqual(translated.tools[0]["function"]["name"], "lookup")

    def test_opencode_channel_does_not_change_generic_chat_channel(self):
        response = self.client.post("/v1/chat/completions", json=self.request(max_tokens=32000))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["object"], "chat.completion")
        self.assertEqual(self.provider.infer.await_count, 1)

    def test_opencode_max_tokens_is_client_only_and_not_provider_facing(self):
        response = self.client.post("/v1/opencode/chat/completions",
                                    json=self.request(max_tokens=32000))

        self.assertEqual(response.status_code, 200)
        forwarded = self.provider.infer.call_args.args[0]
        self.assertIsNone(forwarded.chat.max_tokens)
        self.assertIsNone(forwarded.canonical.max_tokens)
        self.assertEqual(forwarded.client_max_tokens, 32000)

    def test_opencode_gets_a_large_multi_step_context_budget(self):
        response = self.client.post("/v1/opencode/chat/completions", json=self.request())

        self.assertEqual(response.status_code, 200)
        forwarded = self.provider.infer.call_args.args[0]
        self.assertEqual(forwarded.context_budget_chars, main.OPENCODE_CONTEXT_BUDGET_CHARS)
        self.assertGreaterEqual(main.OPENCODE_CONTEXT_BUDGET_CHARS, 1_024)

    def test_generic_route_detects_opencode_client(self):
        response = self.client.post(
            "/v1/chat/completions",
            json=self.request(),
            headers={"user-agent": "OpenCode/1.18.30"},
        )

        self.assertEqual(response.status_code, 200)
        forwarded = self.provider.infer.call_args.args[0]
        self.assertEqual(forwarded.context_budget_chars, main.OPENCODE_CONTEXT_BUDGET_CHARS)

    def test_generic_route_without_opencode_identity_keeps_default_budget(self):
        response = self.client.post("/v1/chat/completions", json=self.request())

        self.assertEqual(response.status_code, 200)
        forwarded = self.provider.infer.call_args.args[0]
        self.assertIsNone(forwarded.context_budget_chars)

    def test_opencode_uses_a_long_watchdog_for_web_generation(self):
        self.assertGreater(main.OPENCODE_STREAM_WATCHDOG_SECONDS, main.STREAM_WATCHDOG_SECONDS)

    def test_openclaw_hint_selects_openclaw_policy_on_shared_route(self):
        response = self.client.post(
            "/v1/chat/completions",
            json=self.request(tools=[{"type": "function", "function": {"name": "computer", "parameters": {}}}]),
        )

        self.assertEqual(response.status_code, 200)
        forwarded = self.provider.infer.call_args.args[0]
        self.assertEqual(forwarded.client_policy, ClientPolicy.OPENCLAW)
        self.assertEqual(forwarded.context_budget_chars, main.OPENCLAW_CONTEXT_BUDGET_CHARS)
        self.assertGreaterEqual(main.OPENCLAW_CONTEXT_BUDGET_CHARS, 1_024)

    def test_opencode_title_request_accepts_large_client_budget_without_provider_call(self):
        response = self.client.post("/v1/opencode/chat/completions", json={
            "model": "deepseek-chat",
            "max_tokens": 32000,
            "messages": [
                {"role": "system", "content": "Generate a concise session title."},
                {"role": "user", "content": "Discuss the release plan"},
            ],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Discuss the release plan")
        self.provider.infer.assert_not_called()

    def test_opencode_max_tokens_rejects_invalid_values_and_conflicts(self):
        for body in (
            self.request(max_tokens=0),
            self.request(max_tokens=-1),
            self.request(max_tokens="32000"),
            self.request(max_tokens=32000, max_completion_tokens=32000),
        ):
            with self.subTest(body=body):
                response = self.client.post("/v1/opencode/chat/completions", json=body)
                self.assertEqual(response.status_code, 400)
        self.provider.infer.assert_not_called()

    def test_malformed_opencode_request_is_safe_and_does_not_reach_provider(self):
        response = self.client.post("/v1/opencode/chat/completions", json={"model": "deepseek-chat"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("requires messages", response.text)
        self.provider.infer.assert_not_called()

    def test_opencode_channel_does_not_advertise_native_features(self):
        response = self.client.get("/v1/models")

        self.assertEqual(response.status_code, 200)
        capabilities = response.json()["data"][0]["capabilities"]
        self.assertEqual(capabilities["tool_calling"], "emulated")
        self.assertFalse(capabilities["parallel_tool_calls"])


if __name__ == "__main__":
    unittest.main()
