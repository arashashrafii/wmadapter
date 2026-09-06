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
    _image_attachments,
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

SHOW_WIDGET_TOOL = [{
    "type": "function",
    "function": {
        "name": "show_widget",
        "description": "Show a visual widget",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "widget_code": {"type": "string"},
            "kind": {"type": "string"},
        }},
    },
}]


class OpenClawCompatibilityTests(unittest.TestCase):
    """Contract tests for the OpenClaw-facing model boundary.

    OpenClaw owns the tools, skills, plugins, channels, and agent runtime;
    these tests verify that MimicGate preserves the model protocol they use.
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

    def test_only_inline_images_are_forwarded_for_web_upload(self):
        inline = "data:image/png;base64,Zm9v"
        attachments = _image_attachments([
            Message(role="user", content=[
                {"type": "text", "text": "describe it"},
                {"type": "image_url", "image_url": {"url": inline}},
                {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
            ])
        ])
        self.assertEqual(attachments, [inline])

    def test_openclaw_image_data_attachment_is_normalized_for_web_upload(self):
        attachments = _image_attachments([
            Message(role="user", content={
                "type": "image",
                "mimeType": "image/webp",
                "data": "Zm9v",
            })
        ])
        self.assertEqual(attachments, ["data:image/webp;base64,Zm9v"])

    def test_nested_openclaw_media_part_is_forwarded(self):
        inline = "data:image/jpeg;base64,Zm9v"
        attachments = _image_attachments([
            Message(role="user", content="describe", media={
                "parts": [{"type": "image_url", "image_url": {"url": inline}}]
            })
        ])
        self.assertEqual(attachments, [inline])

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

    def test_prompt_requires_evidence_driven_self_correction_loop(self):
        prompt = _prompt([Message(role="user", content="Create and verify a project")], tools=EXEC_TOOL)
        self.assertIn("ACTION -> OBSERVE RESULT -> VERIFY", prompt)
        self.assertIn("If verification fails, correct the action and try again", prompt)
        self.assertIn("Never report an application opened without observable evidence", prompt)

    def test_prompt_requires_verification_after_file_or_app_changes(self):
        prompt = _prompt([Message(role="user", content="Open the generated project in VS Code")], tools=EXEC_TOOL)
        self.assertIn("After creating, editing, deleting, or opening something", prompt)
        self.assertIn("inspect or query the resulting state before claiming success", prompt)

    def test_prompt_requires_real_vscode_workspace_evidence(self):
        prompt = _prompt([Message(role="user", content="Open the project in VS Code")], tools=EXEC_TOOL)
        self.assertIn("For VS Code, use `code --status`", prompt)
        self.assertIn("Workspace Stats", prompt)
        self.assertIn("`code --list-extensions` is not proof", prompt)

    def test_prompt_continues_when_command_output_is_pending(self):
        prompt = _prompt([Message(role="user", content="Verify the application")], tools=EXEC_TOOL)
        self.assertIn("Command still running", prompt)
        self.assertIn("poll or retrieve its final output", prompt)
        self.assertIn("Do not treat a pending command as failure or success", prompt)

    def test_prompt_forbids_narrating_a_missing_verification_step(self):
        prompt = _prompt([Message(role="user", content="Finish the verification")], tools=EXEC_TOOL)
        self.assertIn("If evidence is missing, emit the next tool call now", prompt)
        self.assertIn("Do not merely say that you will verify", prompt)

    def test_prompt_forbids_using_bash_code_as_a_tool_call(self):
        prompt = _prompt([Message(role="user", content="Run a command")], tools=EXEC_TOOL)
        self.assertIn("Never use a Bash/code block as a substitute for a tool call", prompt)
        self.assertIn("Do not recommend or execute destructive commands", prompt)

    def test_prompt_does_not_trust_stale_history_as_tool_evidence(self):
        prompt = _prompt([Message(role="user", content="Open the application")], tools=EXEC_TOOL)
        self.assertIn("Treat prior assistant claims, PIDs, and suggested commands as unverified history", prompt)
        self.assertIn("For the current user request, emit the tool call before any explanation", prompt)

    def test_prompt_requires_launch_before_verification_for_open_requests(self):
        prompt = _prompt([Message(role="user", content="Open VS Code")], tools=EXEC_TOOL)
        self.assertIn("Research and capability checks may precede an authorized launch", prompt)
        self.assertIn("A status or `which` check alone does not perform the requested launch", prompt)

    def test_prompt_requires_real_desktop_control_for_gui_requests(self):
        computer_tools = EXEC_TOOL + [{
            "type": "function",
            "function": {
                "name": "computer",
                "description": "See and control the desktop UI",
                "parameters": {"type": "object"},
            },
        }]
        prompt = _prompt(
            [Message(role="user", content="Open VLC with its graphical interface and press Play")],
            tools=computer_tools,
        )
        self.assertIn("DESKTOP GUI POLICY", prompt)
        self.assertIn("computer", prompt)
        self.assertIn("Do not substitute cvlc", prompt)
        self.assertIn("screen.snapshot", prompt)

    def test_prompt_reports_missing_gui_capability_instead_of_faking_success(self):
        prompt = _prompt(
            [Message(role="user", content="Click the Play button in VLC")],
            tools=EXEC_TOOL,
        )
        self.assertIn("and no `computer` tool is listed", prompt)
        self.assertIn("do not claim GUI control", prompt)

    def test_prompt_requires_verified_openclaw_capability_and_command_usage(self):
        prompt = _prompt([Message(role="user", content="Set up desktop control")], tools=EXEC_TOOL)
        self.assertIn("OPENCLAW CAPABILITY CHECK", prompt)
        self.assertIn("verify the exact command", prompt)
        self.assertIn("Do not guess or present an unverified command", prompt)
        self.assertIn("Every user request that asks for an action must end", prompt)

    def test_prompt_requires_self_remediation_before_reporting_a_blocker(self):
        prompt = _prompt([Message(role="user", content="Make desktop control work")], tools=EXEC_TOOL)
        self.assertIn("SELF-REMEDIATION", prompt)
        self.assertIn("attempt safe remediation", prompt)
        self.assertIn("Before reporting a blocker", prompt)
        self.assertIn("verify that remediation changed the capability", prompt)

    def test_openclaw_requests_require_official_docs_research(self):
        prompt = _prompt(
            [Message(role="user", content="How do I fix the OpenClaw node and plugin setup?")],
            tools=EXEC_TOOL,
        )
        self.assertIn("OPENCLAW DOCUMENTATION POLICY", prompt)
        self.assertIn("https://docs.openclaw.ai/", prompt)
        self.assertIn("search the official documentation", prompt)
        self.assertIn("Do not rely on memory", prompt)

    def test_openclaw_requests_use_research_action_verify_loop(self):
        prompt = _prompt(
            [Message(role="user", content="Enable OpenClaw desktop control and test it")],
            tools=EXEC_TOOL,
        )
        self.assertIn("RESEARCH-ACTION-VERIFICATION LOOP", prompt)
        self.assertIn("return the research result", prompt)
        self.assertIn("send the verified action", prompt)
        self.assertIn("retry with a corrected action", prompt)
        self.assertIn("Google search only as a fallback", prompt)

    def test_all_requests_use_evidence_and_consistency_loop(self):
        prompt = _prompt([Message(role="user", content="What is the safest answer?" )], tools=EXEC_TOOL)
        self.assertIn("UNIVERSAL RELIABILITY LOOP", prompt)
        self.assertIn("decompose the question", prompt)
        self.assertIn("cross-check calculations", prompt)
        self.assertIn("Never invent facts", prompt)
        self.assertIn("state uncertainty", prompt)

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

    def test_action_input_tool_call_compatibility_is_structured_and_allowlisted(self):
        call, visible = _extract_tool_call(
            'Action: exec\nAction Input: {"command":"pwd"}',
            EXEC_TOOL,
        )
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"command": "pwd"})
        self.assertEqual(visible, "")

    def test_action_input_unknown_tool_is_not_executed(self):
        call, visible = _extract_tool_call(
            'Action: rm_everything\nAction Input: {}',
            EXEC_TOOL,
        )
        self.assertIsNone(call)
        self.assertIn("Action: rm_everything", visible)

    def test_show_widget_tool_call_is_forwarded_only_when_structured(self):
        call, visible = _extract_tool_call(
            '<tool_call>{"name":"show_widget","arguments":{"title":"Chart","widget_code":"<svg></svg>","kind":"html"}}</tool_call>',
            SHOW_WIDGET_TOOL,
        )
        self.assertEqual(call["function"]["name"], "show_widget")
        self.assertEqual(json.loads(call["function"]["arguments"])["kind"], "html")
        self.assertEqual(visible, "")

    def test_show_widget_markup_in_plain_text_is_not_executed(self):
        call, visible = _extract_tool_call(
            '<show_widget>{"title":"Chart","widget_code":"<svg></svg>"}</show_widget>',
            SHOW_WIDGET_TOOL,
        )
        self.assertIsNone(call)
        self.assertIn("show_widget", visible)

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
            "No tool result is available; completion is not verified.",
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
        self.assertTrue(_prompt([Message(role="user", content=content)]).startswith(f"USER: {content}"))

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
