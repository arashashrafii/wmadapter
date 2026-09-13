import unittest
import json
from wmadapter.providers.protocol import _extract_tool_calls

TOOLS = [
    {"type": "function", "function": {"name": "exec", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "computer", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}}}},
]

class TestProtocolParsing(unittest.TestCase):
    def test_valid_tool_call_xml(self):
        answer = '<tool_call>{"name":"exec","arguments":{"command":"ls"}}</tool_call>'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "exec")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "ls")
        self.assertEqual(visible, "")

    def test_multiple_markers_both_extracted(self):
        answer = '<tool_call>{"name":"exec","arguments":{"command":"cmd1"}}</tool_call>' \
                 ' some text ' \
                 '<tool_call>{"name":"exec","arguments":{"command":"cmd2"}}</tool_call>'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "cmd1")
        self.assertEqual(json.loads(calls[1]["function"]["arguments"])["command"], "cmd2")
        self.assertEqual(visible, "some text")

    def test_deepseek_token_format(self):
        answer = (
            "<｜tool▁call▁begin｜>computer"
            "<｜tool▁call▁argument▁begin｜>{\"action\":\"screenshot\"}"
            "<｜tool▁call▁argument▁end｜><｜tool▁call▁end｜>"
        )
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "computer")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["action"], "screenshot")
        self.assertEqual(visible, "")

    def test_dsml_and_surrounding_text_are_preserved(self):
        answer = 'before <｜｜DSML｜｜ invoke name="exec"><｜｜DSML｜｜ parameter name="command">pwd</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke> after'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(visible, "before  after")

    def test_malformed_json_is_not_executed_or_discarded(self):
        answer = 'prefix <tool_call>{"name":"exec","arguments":{bad}}</tool_call> suffix'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(calls, [])
        self.assertEqual(visible, answer)

    def test_unknown_tool_is_not_executed_or_discarded(self):
        answer = '<tool_call>{"name":"unknown","arguments":{}}</tool_call>'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(calls, [])
        self.assertEqual(visible, answer)

    def test_incomplete_marker_is_preserved(self):
        answer = 'text <tool_call>{"name":"exec"}'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(calls, [])
        self.assertEqual(visible, answer)

    def test_ordinary_json_like_text_is_preserved(self):
        answer = 'The example is {"name":"exec","arguments":{"command":"ls"}}.'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(calls, [])
        self.assertEqual(visible, answer)

    def test_non_object_arguments_do_not_raise(self):
        answer = '<tool_call>{"name":"exec","arguments":[]}</tool_call>'
        calls, visible = _extract_tool_calls(answer, TOOLS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {})
        self.assertEqual(visible, "")

    # ... (other tests updated similarly to expect len(calls) and check visible text)
