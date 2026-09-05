import unittest

from webbridgefreeride.providers.normalizer import ToolProtocolNormalizer
from webbridgefreeride.providers.policy import ClientPolicy, detect_client_policy


class PolicyLayerTests(unittest.TestCase):
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
