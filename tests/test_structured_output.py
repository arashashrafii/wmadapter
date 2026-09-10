import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import (
    ModelCapabilities,
    ProviderResult,
    normalize_structured_output,
    validate_structured_output,
)
from wmadapter.providers.router import ProviderRouter


class StructuredProvider(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat",)
    capabilities = ModelCapabilities()

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {"ready": True}

    async def complete(self, prompt, conversation_id=None):
        return '{"answer":"repaired"}'


class StructuredOutputTests(unittest.TestCase):
    def setUp(self):
        self.provider = StructuredProvider()
        self.provider.infer = AsyncMock(return_value=ProviderResult(content='{"answer":"ok"}'))
        self.provider.complete = AsyncMock(return_value='{"answer":"repaired"}')
        self.router_patch = patch.object(main, "router", ProviderRouter({"deepseek": self.provider}, "deepseek"))
        self.router_patch.start()
        self.addCleanup(self.router_patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def post_chat(self, **overrides):
        body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hello"}]}
        body.update(overrides)
        return self.client.post("/v1/chat/completions", json=body)

    def test_json_object_and_schema_shapes_preserve_metadata(self):
        object_spec = normalize_structured_output({"type": "json_object"})
        schema_input = {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "description": "Preserve this metadata",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
        }
        schema_spec = normalize_structured_output(schema_input)

        self.assertEqual(object_spec.type, "json_object")
        self.assertEqual(schema_spec.metadata["description"], "Preserve this metadata")
        self.assertTrue(schema_spec.strict)
        self.assertEqual(schema_spec.schema["required"], ["answer"])

    def test_schema_output_is_validated_and_passed_to_provider(self):
        response = self.post_chat(response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
            },
        })

        self.assertEqual(response.status_code, 200)
        request = self.provider.infer.call_args.args[0]
        self.assertEqual(request.structured_output.name, "answer")
        self.assertEqual(response.json()["choices"][0]["message"]["content"], '{"answer":"ok"}')

    def test_invalid_output_allows_one_bounded_repair(self):
        self.provider.infer = AsyncMock(return_value=ProviderResult(content="not json"))
        response = self.post_chat(response_format={"type": "json_object"})

        self.assertEqual(response.status_code, 200)
        self.provider.infer.assert_awaited_once()
        self.provider.complete.assert_awaited_once()
        self.assertEqual(response.json()["choices"][0]["message"]["content"], '{"answer":"repaired"}')

    def test_second_invalid_output_fails_without_retry_loop(self):
        self.provider.infer = AsyncMock(return_value=ProviderResult(content="bad"))
        self.provider.complete = AsyncMock(return_value="still bad")
        response = self.post_chat(response_format={"type": "json_object"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("structured_output_invalid", response.text)
        self.provider.complete.assert_awaited_once()

    def test_unsupported_schema_features_are_rejected_before_provider(self):
        response = self.post_chat(response_format={
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": {"$ref": "#/definitions/answer"}},
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported JSON schema feature", response.text)
        self.provider.infer.assert_not_called()

    def test_plain_text_and_tools_remain_unstructured(self):
        response = self.post_chat()

        self.assertEqual(response.status_code, 200)
        self.provider.complete.assert_not_called()

    def test_structured_value_validation_rejects_schema_mismatch(self):
        spec = normalize_structured_output({
            "type": "json_schema",
            "name": "answer",
            "schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
        })
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_structured_output("{}", spec)


if __name__ == "__main__":
    unittest.main()
