import unittest

from wmadapter.providers.contract import (
    ChatRequest,
    ConversationId,
    Message,
    ProviderRequest,
    ResponseId,
    canonicalize,
)
from wmadapter.providers.state import (
    ConversationStateConflict,
    GatewayState,
    UnknownResponseId,
)


class GatewayStateTests(unittest.TestCase):
    def test_same_prompt_isolated_by_explicit_conversation(self):
        state = GatewayState()
        first = ConversationId("conversation-a")
        second = ConversationId("conversation-b")
        state.remember(ResponseId("response-a"), first)
        state.remember(ResponseId("response-b"), second)

        self.assertEqual(state.resolve(previous_response_id=ResponseId("response-a")), first)
        self.assertEqual(state.resolve(previous_response_id=ResponseId("response-b")), second)

    def test_explicit_identifiers_are_preserved_in_contract(self):
        request = ChatRequest(
            messages=[Message(role="user", content="same prompt")],
            conversation_id="conversation-a",
            previous_response_id="response-a",
        )
        canonical = canonicalize(request)
        provider_request = ProviderRequest(
            chat=request,
            canonical=canonical,
            conversation_id=ConversationId("conversation-a"),
            previous_response_id=ResponseId("response-a"),
        )

        self.assertEqual(canonical.conversation_id, "conversation-a")
        self.assertEqual(canonical.previous_response_id, "response-a")
        self.assertEqual(provider_request.previous_response_id, "response-a")

    def test_unknown_and_expired_response_ids_are_rejected(self):
        state = GatewayState()
        with self.assertRaises(UnknownResponseId):
            state.resolve(previous_response_id=ResponseId("missing"))

        response_id = ResponseId("expiring-response")
        state.remember(response_id, ConversationId("conversation"))
        self.assertTrue(state.expire(response_id))
        self.assertFalse(state.expire(response_id))
        with self.assertRaises(UnknownResponseId):
            state.resolve(previous_response_id=response_id)

    def test_conflicting_explicit_ids_are_rejected(self):
        state = GatewayState()
        state.remember(ResponseId("response-a"), ConversationId("conversation-a"))

        with self.assertRaises(ConversationStateConflict):
            state.resolve(
                conversation_id=ConversationId("conversation-b"),
                previous_response_id=ResponseId("response-a"),
            )

    def test_restart_clears_only_ephemeral_state(self):
        state = GatewayState()
        state.remember(ResponseId("response"), ConversationId("conversation"))
        self.assertEqual(len(state), 1)

        state.clear()

        self.assertEqual(len(state), 0)
        with self.assertRaises(UnknownResponseId):
            state.resolve(previous_response_id=ResponseId("response"))

    def test_state_contains_no_prompt_or_credential_data(self):
        state = GatewayState()
        state.remember(ResponseId("response"), ConversationId("conversation"))

        self.assertNotIn("same prompt", repr(state))
        self.assertNotIn("api_key", repr(state))
        self.assertNotIn("transcript", repr(state))


if __name__ == "__main__":
    unittest.main()
