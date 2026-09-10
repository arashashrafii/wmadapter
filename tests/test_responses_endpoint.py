import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import ProviderResult
from wmadapter.providers.router import ProviderRouter


class FakeResponsesProvider(ChatProvider):
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


class ResponsesEndpointTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeResponsesProvider()
        self.provider.infer = AsyncMock(return_value=type("Result", (), {"content": "hello", "tool_calls": []})())
        self.router_patch = patch.object(main, "router", ProviderRouter({"deepseek": self.provider}, "deepseek"))
        self.router_patch.start()
        main.response_state.clear()
        self.addCleanup(main.response_state.clear)
        self.addCleanup(self.router_patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def post(self, **overrides):
        body = {"model": "deepseek-chat", "input": "hello"}
        body.update(overrides)
        return self.client.post("/v1/responses", json=body)

    def test_text_response_has_standard_envelope_and_continuation_id(self):
        response = self.post()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "response")
        self.assertTrue(body["id"].startswith("resp_"))
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output_text"], "hello")
        self.assertIsNone(body["usage"])
        self.assertEqual(body["output"][0]["content"][0]["type"], "output_text")
        self.assertTrue(body["conversation_id"].startswith("resp-"))

    def test_observed_usage_is_returned_without_gateway_estimation(self):
        self.provider.infer = AsyncMock(return_value=ProviderResult(
            content="hello",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        ))
        body = self.post().json()
        self.assertEqual(body["usage"], {
            "prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5
        })

    def test_previous_response_id_continues_same_local_conversation(self):
        first = self.post().json()
        second = self.post(input="second", previous_response_id=first["id"])

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["conversation_id"], first["conversation_id"])
        self.assertEqual(self.provider.infer.await_args_list[1].args[0].conversation_id, first["conversation_id"])

    def test_explicit_conversation_is_preserved(self):
        response = self.post(conversation_id="client-conversation")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["conversation_id"], "client-conversation")
        self.assertEqual(self.provider.infer.call_args.args[0].conversation_id, "client-conversation")

    def test_unknown_previous_response_is_explicitly_rejected(self):
        response = self.post(previous_response_id="resp_missing")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown or expired previous_response_id", response.text)
        self.provider.infer.assert_not_called()

    def test_unsupported_features_are_explicitly_rejected(self):
        for feature in (
            {"stream": True},
            {"tools": []},
            {"input": [{"type": "input_image", "image_url": "https://example.com/a.png"}]},
            {"modalities": ["text"]},
        ):
            with self.subTest(feature=feature):
                response = self.post(**feature)
                self.assertEqual(response.status_code, 400)
                self.assertIn("not supported", response.text.lower())
        self.provider.infer.assert_not_called()

    def test_tool_forms_and_choice_are_explicitly_unsupported(self):
        for field, value in (
            ('tools', []), ('tool_choice', 'auto'), ('parallel_tool_calls', False),
        ):
            with self.subTest(field=field):
                response = self.post(**{field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn('tool', response.text.lower())
        self.provider.infer.assert_not_called()

    def test_sampling_controls_are_explicitly_rejected(self):
        for field, value in (
            ('temperature', 0.2), ('top_p', 0.9), ('max_output_tokens', 20),
            ('presence_penalty', 0.1), ('frequency_penalty', 0.1),
            ('seed', 7), ('stop', 'END'),
        ):
            with self.subTest(field=field):
                response = self.post(**{field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn('sampling control', response.text.lower())
        self.provider.infer.assert_not_called()

    def test_state_is_cleared_for_restart(self):
        first = self.post().json()
        main.response_state.clear()

        response = self.post(previous_response_id=first["id"])

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown or expired previous_response_id", response.text)


if __name__ == "__main__":
    unittest.main()
