import unittest

from wmadapter.providers.policy import ClientPolicy, detect_client_policy
from wmadapter.providers.protocol import _prompt
from wmadapter.providers.contract import Message


TOOL_SEARCH_TOOLS = [
    {"type": "function", "function": {"name": "tool_search", "parameters": {"type": "object"}}},
    {"type": "function", "function": {"name": "tool_describe", "parameters": {"type": "object"}}},
    {"type": "function", "function": {"name": "tool_call", "parameters": {"type": "object"}}},
]


class OpenClawToolSearchTests(unittest.TestCase):
    def test_structured_tool_search_surface_selects_openclaw_policy(self):
        self.assertEqual(
            detect_client_policy([], TOOL_SEARCH_TOOLS),
            ClientPolicy.OPENCLAW,
        )

    def test_prompt_preserves_structured_tool_search_names(self):
        prompt = _prompt(
            [Message(role="user", content="Find the right tool")],
            tools=TOOL_SEARCH_TOOLS,
            client_policy=ClientPolicy.OPENCLAW,
        )
        self.assertIn('"name":"tool_search"', prompt)
        self.assertIn('"name":"tool_describe"', prompt)
        self.assertIn('"name":"tool_call"', prompt)
        self.assertIn("CURRENT TOOL CATALOG (authoritative for this request)", prompt)

    def test_browser_prompt_stops_after_successful_verified_entry(self):
        prompt = _prompt(
            [Message(role="user", content="Open Google and type a search")],
            tools=[{"type": "function", "function": {"name": "browser", "parameters": {"type": "object"}}}],
            client_policy=ClientPolicy.OPENCLAW,
        )
        self.assertIn("BROWSER COMPLETION", prompt)
        self.assertIn("Do not call snapshot or text", prompt)


if __name__ == "__main__":
    unittest.main()
