from __future__ import annotations

import json
import unittest

from webbridgefreeride.main import (
    ChatRequest,
    Message,
    _completion_response,
    _clean_renderer_artifacts,
    _extract_tool_call,
    _fallback_after_tool,
    _prompt,
    _sse,
)


EXEC_TOOL = [{
    "type": "function",
    "function": {
        "name": "exec",
        "description": "Run a command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
    },
}]


class OpenClawCompatibilityTests(unittest.TestCase):
    """Contract tests for the OpenClaw-facing model boundary.

    OpenClaw owns the tools, skills, plugins, channels, and agent runtime;
    these tests verify that WebBridge preserves the model protocol they use.
    """

    def test_openclaw_extra_request_fields_are_preserved(self):
        request = ChatRequest.model_validate({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "hi", "sender": "owner"}],
            "stream": True,
            "tools": EXEC_TOOL,
            "tool_choice": "auto",
            "metadata": {"session": "agent:main:test"},
        })
        self.assertTrue(request.stream)
        self.assertEqual(request.messages[0].sender, "owner")
        self.assertEqual(request.metadata["session"], "agent:main:test")

    def test_assistant_tool_calls_and_tool_results_round_trip_into_prompt(self):
        messages = [
            Message(role="user", content="Check status"),
            Message(role="assistant", content=None, tool_calls=[{
                "id": "call_1", "type": "function",
                "function": {"name": "exec", "arguments": '{"command":"uptime"}'},
            }]),
            Message(role="tool", tool_call_id="call_1", content="up 1 day"),
        ]
        prompt = _prompt(messages, tools=EXEC_TOOL)
        self.assertIn('ASSISTANT_TOOL_CALL: {"name": "exec"', prompt)
        self.assertIn("TOOL_RESULT (call_1): up 1 day", prompt)
        self.assertIn("A tool result is already available above", prompt)

    def test_prompt_explains_openclaw_tool_skill_and_plugin_selection(self):
        prompt = _prompt([Message(role="user", content="Open a web page")], tools=EXEC_TOOL)
        self.assertIn("use browser for browser/web automation", prompt)
        self.assertIn("Skills provide workflow instructions", prompt)
        self.assertIn("plugins provide extra tools", prompt)
        self.assertIn("Never claim that a task was completed", prompt)

    def test_browser_prompt_documents_fill_shape(self):
        browser_tool = [{"type": "function", "function": {"name": "browser"}}]
        prompt = _prompt([Message(role="user", content="Log in")], tools=browser_tool)
        self.assertIn("fields:[{ref:<textbox ref>,text:<value>}]", prompt)
        self.assertIn("Never send fill ref/text as top-level fields", prompt)

    def test_valid_tool_call_is_structured_and_not_visible(self):
        call, visible = _extract_tool_call(
            '<tool_call>{"name":"exec","arguments":{"command":"pwd"}}</tool_call>',
            EXEC_TOOL,
        )
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": "pwd"})
        self.assertEqual(visible, "")

    def test_legacy_tool_call_is_supported(self):
        call, visible = _extract_tool_call(
            '<function_calls><invoke name="exec"><parameter name="command">pwd</parameter></invoke></function_calls>',
            EXEC_TOOL,
        )
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": "pwd"})
        self.assertEqual(visible, "")

    def test_browser_fill_legacy_shape_is_normalized_only_for_browser(self):
        browser_tool = [{"type": "function", "function": {"name": "browser"}}]
        call, visible = _extract_tool_call(
            '<tool_call>{"name":"browser","arguments":{"action":"act","targetId":"tab-1","kind":"fill","ref":"e21","text":"09128337571"}}</tool_call>',
            browser_tool,
        )
        self.assertEqual(
            json.loads(call["function"]["arguments"]),
            {
                "action": "act",
                "targetId": "tab-1",
                "kind": "fill",
                "fields": [{"ref": "e21", "text": "09128337571"}],
            },
        )
        self.assertEqual(visible, "")

    def test_other_tools_are_not_coerced(self):
        call, _ = _extract_tool_call(
            '<tool_call>{"name":"exec","arguments":{"action":"act","kind":"fill","ref":"e21","text":"x"}}</tool_call>',
            EXEC_TOOL,
        )
        self.assertEqual(
            json.loads(call["function"]["arguments"]),
            {"action": "act", "kind": "fill", "ref": "e21", "text": "x"},
        )

    def test_markdown_tool_json_is_hidden_from_assistant_content(self):
        call, visible = _extract_tool_call(
            'json\nCopy\nDownload\n```json\n{"name":"exec","arguments":{"command":"pwd"}}\n```',
            EXEC_TOOL,
        )
        self.assertIsNotNone(call)
        self.assertEqual(visible, "json\nCopy\nDownload")

    def test_plain_bash_markdown_is_never_executed(self):
        call, visible = _extract_tool_call("bash\nCopy\nDownload\nrm -rf /tmp/example", EXEC_TOOL)
        self.assertIsNone(call)
        self.assertIn("rm -rf", visible)

    def test_tool_choice_none_disables_tool_protocol_prompt(self):
        prompt = _prompt([Message(role="user", content="show JSON")], tools=None)
        self.assertNotIn("FINAL TOOL PROTOCOL", prompt)

    def test_empty_final_answer_uses_last_real_tool_result(self):
        messages = [Message(role="user", content="Run it"), Message(role="tool", content="EXEC_OK")]
        self.assertEqual(_fallback_after_tool(messages, ""), "Tool result:\nEXEC_OK")
        self.assertEqual(_fallback_after_tool(messages, "Final answer"), "Final answer")

    def test_empty_final_answer_after_tool_call_is_not_blank(self):
        messages = [Message(role="assistant", content=None, tool_calls=[{
            "id": "call_1", "function": {"name": "process", "arguments": "{}"}
        }])]
        self.assertEqual(
            _fallback_after_tool(messages, ""),
            "The requested tool completed, but no output was returned.",
        )

    def test_renderer_labels_are_removed_from_tool_result_text(self):
        self.assertEqual(
            _clean_renderer_artifacts("text\nCopy\nDownload\nEDITED_STEP02"),
            "EDITED_STEP02",
        )
        self.assertEqual(
            _clean_renderer_artifacts("The edit succeeded:\n\ntext\nCopy\nDownload\nEDITED_STEP02"),
            "The edit succeeded:\n\nEDITED_STEP02",
        )
        self.assertEqual(_clean_renderer_artifacts("# عنوان\n\nسلام"), "# عنوان\n\nسلام")

    def test_standard_tool_completion_has_openai_finish_reason(self):
        response = _completion_response("req-1", "deepseek-chat", "", [{
            "id": "call_1", "type": "function",
            "function": {"name": "exec", "arguments": '{"command":"pwd"}'},
        }])
        choice = response["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertIsNone(choice["message"]["content"])
        self.assertEqual(choice["message"]["tool_calls"][0]["type"], "function")

    def test_stream_event_is_valid_sse(self):
        event = _sse({"choices": [{"delta": {"content": "سلام"}}]})
        self.assertTrue(event.startswith("data: "))
        self.assertTrue(event.endswith("\n\n"))
        self.assertEqual(json.loads(event[6:]), {"choices": [{"delta": {"content": "سلام"}}]})

    def test_persian_and_markdown_content_remain_unchanged(self):
        content = "# پاسخ\n\nسلام ایران 🇮🇷\n\n```python\nprint('ok')\n```"
        self.assertEqual(_prompt([Message(role="user", content=content)]), f"USER: {content}")

    def test_conversation_history_is_kept_in_order(self):
        prompt = _prompt([
            Message(role="user", content="first"),
            Message(role="assistant", content="second"),
            Message(role="user", content="third"),
        ])
        self.assertLess(prompt.index("USER: first"), prompt.index("ASSISTANT: second"))
        self.assertLess(prompt.index("ASSISTANT: second"), prompt.index("USER: third"))


if __name__ == "__main__":
    unittest.main()
