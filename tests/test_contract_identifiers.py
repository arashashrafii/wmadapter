import unittest

from wmadapter.providers.contract import (
    CanonicalRequest,
    ChatRequest,
    ConversationId,
    Message,
    ProviderRequest,
    ProviderResult,
    ResponseId,
    canonicalize,
)


class ContractIdentifierTests(unittest.TestCase):
    def test_identifier_types_are_opaque_strings(self):
        response_id = ResponseId("chatcmpl-provider-owned-token")
        conversation_id = ConversationId("session/from-the-client")

        self.assertIsInstance(response_id, str)
        self.assertIsInstance(conversation_id, str)
        self.assertEqual(response_id, "chatcmpl-provider-owned-token")
        self.assertEqual(conversation_id, "session/from-the-client")

    def test_provider_result_round_trips_opaque_identifiers(self):
        result = ProviderResult(
            response_id=ResponseId("opaque-response"),
            conversation_id=ConversationId("opaque-conversation"),
            content="answer",
        )

        self.assertEqual(result.response_id, "opaque-response")
        self.assertEqual(result.conversation_id, "opaque-conversation")
        self.assertEqual(
            result.model_dump(),
            {
                "response_id": "opaque-response",
                "conversation_id": "opaque-conversation",
                "content": "answer",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": None,
            },
        )

    def test_request_and_canonical_contract_preserve_identifier(self):
        request = ChatRequest(
            messages=[Message(role="user", content="hello")],
            conversation_id="client-supplied-id",
        )
        canonical = canonicalize(request)
        provider_request = ProviderRequest(
            chat=request,
            canonical=canonical,
            conversation_id=ConversationId("client-supplied-id"),
        )

        self.assertEqual(canonical.conversation_id, "client-supplied-id")
        self.assertEqual(provider_request.conversation_id, "client-supplied-id")
        self.assertIsInstance(canonical, CanonicalRequest)

    def test_identifiers_do_not_capture_credentials_or_transcripts(self):
        result = ProviderResult(response_id=ResponseId("response-id"))

        self.assertNotIn("api_key", result.model_dump())
        self.assertNotIn("credential", result.model_dump())
        self.assertNotIn("messages", result.model_dump())
        self.assertNotIn("transcript", result.model_dump())


if __name__ == "__main__":
    unittest.main()
