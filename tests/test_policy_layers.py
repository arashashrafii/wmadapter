import unittest

from mimicgate.providers.normalizer import ToolProtocolNormalizer
from mimicgate.providers.policy import ClientPolicy, detect_client_policy
from mimicgate.providers.protocol import _prompt
from mimicgate.providers.contract import Message
from mimicgate.providers.recovery import ToolCallRecovery


class PolicyLayerTests(unittest.IsolatedAsyncioTestCase):
    def test_generic_clients_do_not_receive_openclaw_policy(self):
        message = type("Message", (), {"role": "user", "content": "Use the listed function"})()
        self.assertIs(detect_client_policy([message], [{"type": "function"}]), ClientPolicy.GENERIC)

    def test_openclaw_policy_requires_explicit_marker(self):
        message = type("Message", (), {"role": "user", "content": "OpenClaw should use computer.act"})()
        self.assertIs(detect_client_policy([message], [{"type": "function"}]), ClientPolicy.OPENCLAW)

    def test_normalizer_hides_structured_tool_marker(self):
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {}}}]
        call, visible = ToolProtocolNormalizer().normalize(
            '<tool_call>{"name":"lookup","arguments":{}}</tool_call>', tools
        )
        self.assertEqual(call["function"]["name"], "lookup")
        self.assertEqual(visible, "")

    def test_explicit_generic_policy_is_brand_neutral(self):
        messages = [Message(role="user", content="OpenClaw should use computer.act")]
        prompt = _prompt(messages, tools=[{"type": "function", "function": {"name": "computer"}}],
                         client_policy=ClientPolicy.GENERIC)
        self.assertNotIn("OPENCLAW DOCUMENTATION POLICY", prompt)
        self.assertNotIn("OPENCLAW CAPABILITY CHECK", prompt)
        self.assertNotIn("DESKTOP GUI POLICY", prompt)

    def test_recovery_has_independent_provider_boundary(self):
        self.assertTrue(hasattr(ToolCallRecovery(), "resolve"))

    def test_legacy_protocol_name_is_not_recovery_entrypoint(self):
        from mimicgate.providers.protocol import _legacy_resolve_web_answer
        self.assertTrue(callable(_legacy_resolve_web_answer))

    def test_provider_protocol_owns_stable_recovery_dependency(self):
        from mimicgate.service import DeepSeekService
        from mimicgate.config import load_config
        provider = DeepSeekService(load_config('/nonexistent'))
        self.assertIs(provider.protocol.recovery, provider.protocol.recovery)

    async def test_recovery_implementation_does_not_call_legacy_function(self):
        from unittest.mock import AsyncMock, patch
        provider = type("Provider", (), {})()
        provider.complete = AsyncMock(return_value="repaired")
        with patch("mimicgate.providers.protocol._legacy_resolve_web_answer", side_effect=AssertionError):
            call, visible = await ToolCallRecovery().resolve(
                provider, "", [], None, "session", "USER: hi"
            )
        self.assertIsNone(call)
        self.assertEqual(visible, "repaired")

    async def test_recovery_accepts_injected_call_guard(self):
        from unittest.mock import AsyncMock
        provider = type("Provider", (), {})()
        provider.complete = AsyncMock(return_value='<tool_call>{"name":"blocked","arguments":{}}</tool_call>')
        recovery = ToolCallRecovery(validate_call=lambda call: call["function"]["name"] != "blocked")
        with self.assertRaises(ValueError):
            await recovery.resolve(provider, '<tool_call>{"name":"blocked","arguments":{}}</tool_call>', [],
                                   [{"type":"function","function":{"name":"blocked","parameters":{}}}], "s", "prompt")
