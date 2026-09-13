import json
import unittest
from unittest.mock import AsyncMock
import re

from wmadapter.main import Message, _resolve_web_answer


TOOLS = [{"type": "function", "function": {"name": "exec"}}]


class ProtocolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_guided_tool_arguments_stringify_object_for_string_field(self):
        from wmadapter.providers.protocol import _extract_tool_call

        tools = [{"type": "function", "function": {
            "name": "write",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            }},
        }}]
        answer = '<tool_call>{"name":"write","arguments":{"path":"package.json","content":{"name":"iran-smb-accounting"}}}</tool_call>'
        call, visible = _extract_tool_call(answer, tools)

        self.assertEqual(visible, "")
        self.assertIsNotNone(call)
        self.assertEqual(
            json.loads(call["function"]["arguments"])["content"],
            '{"name":"iran-smb-accounting"}',
        )

    def test_deepseek_tool_token_format_becomes_openai_tool_call(self):
        from wmadapter.providers.protocol import _extract_tool_call

        answer = (
            "<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>computer"
            "<｜tool▁call▁argument▁begin｜>{\"action\":\"screenshot\"}"
            "<｜tool▁call▁argument▁end｜><｜tool▁call▁end｜>"
            "<｜tool▁calls▁end｜>"
        )
        call, visible = _extract_tool_call(answer, [{
            "type": "function",
            "function": {"name": "computer", "parameters": {"type": "object"}},
        }])

        self.assertEqual(visible, "")
        self.assertIsNotNone(call)
        self.assertEqual(call["function"]["name"], "computer")
        self.assertEqual(json.loads(call["function"]["arguments"])["action"], "screenshot")

    async def test_duplicate_action_after_identical_results_requests_new_strategy(self):
        from wmadapter.main import _extract_tool_call
        answer = '<tool_call>{"name":"exec","arguments":{"command":"nc localhost 8080"}}</tool_call>'
        messages = [Message(role="user", content="Control VLC")]
        for _ in range(2):
            call, _ = _extract_tool_call(answer, TOOLS)
            messages.extend([Message(role="assistant", tool_calls=[call]), Message(role="tool", tool_call_id=call["id"], content="Apache 400 Bad Request")])
        provider = AsyncMock()
        provider.complete.return_value = answer
        with self.assertRaises(ValueError):
            await _resolve_web_answer(provider, answer, messages, TOOLS, "a", "context")
        self.assertEqual(provider.complete.await_count, 1)

    async def test_narrated_action_is_reasked_not_executed_as_prose(self):
        provider = AsyncMock()
        call, text = await _resolve_web_answer(
            provider, 'I will run it.\nAction: exec\nAction Input: {"command":"pwd"}',
            [Message(role="user", content="Run pwd")], TOOLS, "session-a", "original context",
        )
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(text, "I will run it.")
        provider.complete.assert_not_awaited()

    async def test_narrated_gui_plan_is_reasked_for_computer_call(self):
        provider = AsyncMock()
        provider.complete.return_value = '<tool_call>{"name":"computer","arguments":{"action":"screenshot"}}</tool_call>'
        answer = "ابتدا وضعیت دسکتاپ را بررسی می‌کنم، سپس برنامه را باز می‌کنم و در نهایت نتیجه را تأیید می‌کنم."
        call, text = await _resolve_web_answer(
            provider, answer, [Message(role="user", content="VLC را با رابط گرافیکی باز کن")],
            [{"type": "function", "function": {"name": "computer"}}], "session-gui", "original context",
        )
        self.assertEqual(call["function"]["name"], "computer")
        self.assertEqual(text, "")
        self.assertIn("emit the structured tool call immediately", provider.complete.call_args.args[0])

    async def test_explicit_gui_constraint_blocks_headless_exec_call(self):
        provider = AsyncMock()
        provider.complete.return_value = '<tool_call>{"name":"computer","arguments":{"action":"screenshot"}}</tool_call>'
        answer = '<tool_call>{"name":"exec","arguments":{"command":"vlc --no-video file.mp3"}}</tool_call>'
        call, text = await _resolve_web_answer(
            provider, answer, [Message(role="user", content="VLC را با رابط گرافیکی باز کن؛ از --no-video و headless استفاده نکن")],
            [{"type": "function", "function": {"name": "exec"}}, {"type": "function", "function": {"name": "computer"}}],
            "session-gui", "original context",
        )
        self.assertEqual(call["function"]["name"], "computer")
        self.assertEqual(text, "")

    async def test_empty_post_tool_reply_recovered_with_result_context(self):
        provider = AsyncMock()
        provider.complete.return_value = "The result is 42."
        call, text = await _resolve_web_answer(
            provider, "", [Message(role="tool", content="42")], TOOLS, "a", "TOOL_RESULT: 42",
        )
        self.assertIsNone(call)
        self.assertEqual(text, "The result is 42.")
        self.assertIn("TOOL_RESULT: 42", provider.complete.call_args.args[0])

    async def test_persistent_failure_stops_after_one_repair(self):
        provider = AsyncMock()
        provider.complete.return_value = ""
        with self.assertRaises(ValueError):
            await _resolve_web_answer(provider, "", [], TOOLS, "a", "context")
        self.assertEqual(provider.complete.await_count, 1)

    async def test_normal_answer_and_disabled_tools_do_not_retry(self):
        provider = AsyncMock()
        for tools, answer in [(TOOLS, "2 + 2 = 4"), (None, 'Action: exec\nAction Input: {}')]:
            call, text = await _resolve_web_answer(provider, answer, [], tools, "a", "context")
            self.assertIsNone(call)
            self.assertEqual(text, answer)
        provider.complete.assert_not_awaited()

    async def test_normal_bypass_emits_redacted_observability_without_payload(self):
        provider = AsyncMock()
        answer = "ordinary private-looking response"
        with self.assertLogs("wmadapter.providers.recovery", level="INFO") as logs:
            call, text = await _resolve_web_answer(provider, answer, [], TOOLS, "a", "private prompt")
        self.assertIsNone(call)
        self.assertEqual(text, answer)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("reason=initial_complete", logs.output[0])
        self.assertIn("outcome=bypass", logs.output[0])
        self.assertRegex(logs.output[0], r"length=\d+ sha256=[0-9a-f]{64}")
        self.assertNotIn(answer, logs.output[0])
        self.assertNotIn("private prompt", logs.output[0])

    async def test_valid_repaired_final_and_tool_are_observable(self):
        provider = AsyncMock()
        provider.complete.return_value = "repaired final"
        with self.assertLogs("wmadapter.providers.recovery", level="INFO") as logs:
            call, text = await _resolve_web_answer(provider, "", [], TOOLS, "a", "context")
        self.assertIsNone(call)
        self.assertEqual(text, "repaired final")
        self.assertIn("reason=repair_complete", logs.output[-1])
        self.assertIn("outcome=repaired_final", logs.output[-1])

        provider.complete.return_value = '<tool_call>{"name":"exec","arguments":{"command":"pwd"}}</tool_call>'
        with self.assertLogs("wmadapter.providers.recovery", level="INFO") as logs:
            call, text = await _resolve_web_answer(provider, "", [], TOOLS, "a", "context")
        self.assertEqual(call["function"]["name"], "exec")
        self.assertEqual(text, "")
        self.assertIn("reason=repair_valid_tool_call", logs.output[-1])
        self.assertIn("outcome=repaired_tool", logs.output[-1])

    async def test_invalid_repaired_result_logs_reason_and_fails_closed_once(self):
        provider = AsyncMock()
        provider.complete.return_value = "<tool_call>{bad}</tool_call>"
        with self.assertLogs("wmadapter.providers.recovery", level="INFO") as logs:
            with self.assertRaisesRegex(ValueError, "after one repair"):
                await _resolve_web_answer(provider, "", [], TOOLS, "a", "context")
        self.assertEqual(provider.complete.await_count, 1)
        self.assertIn("reason=repair_unresolved_marker", logs.output[-1])
        self.assertIn("outcome=fail_closed", logs.output[-1])
        self.assertNotIn("bad", logs.output[-1])

    async def test_oversized_repair_context_is_compacted(self):
        provider = AsyncMock()
        provider.complete.return_value = "repaired"
        prompt = "A" * 20000
        await _resolve_web_answer(provider, "", [], TOOLS, "a", prompt)
        repair_prompt = provider.complete.call_args.args[0]
        self.assertLess(len(repair_prompt), len(prompt))
        self.assertIn("[REPAIR CONTEXT COMPACTED]", repair_prompt)
        self.assertEqual(provider.complete.await_count, 1)

    async def test_provider_repair_api_receives_primary_id_without_using_primary_history(self):
        from wmadapter.providers.recovery import ToolCallRecovery
        class IsolatedProvider:
            def __init__(self):
                self.repair_calls = []
                self.complete = AsyncMock()

            async def repair_complete(self, prompt, conversation_id=None):
                self.repair_calls.append((prompt, conversation_id))
                return "repaired"

        provider = IsolatedProvider()
        call, text = await ToolCallRecovery().resolve(
            provider, "", [], TOOLS, "primary-session", "context"
        )
        self.assertIsNone(call)
        self.assertEqual(text, "repaired")
        self.assertEqual(len(provider.repair_calls), 1)
        self.assertEqual(provider.repair_calls[0][1], "primary-session")
        provider.complete.assert_not_awaited()

    async def test_legacy_provider_gets_isolated_fallback_id_including_anonymous_turn(self):
        from wmadapter.providers.recovery import ToolCallRecovery
        provider = AsyncMock()
        provider.complete.return_value = "repaired"
        await ToolCallRecovery().resolve(provider, "", [], TOOLS, None, "context")
        repair_id = provider.complete.call_args.kwargs["conversation_id"]
        self.assertTrue(repair_id.startswith("repair:"))
        self.assertNotEqual(repair_id, None)

    async def test_research_action_verification_round_trip(self):
        provider = AsyncMock()
        messages = [Message(role="user", content="Research, act, and verify")]
        from wmadapter.main import _prompt
        for command, result in [("research", "official instructions"), ("action", "started"), ("verify", "confirmed")]:
            answer = '<tool_call>{"name":"exec","arguments":{"command":"' + command + '"}}</tool_call>'
            call, _ = await _resolve_web_answer(provider, answer, messages, TOOLS, "a", _prompt(messages, tools=TOOLS))
            messages.extend([Message(role="assistant", tool_calls=[call]), Message(role="tool", tool_call_id=call["id"], content=result)])
        prompt = _prompt(messages, tools=TOOLS)
        self.assertIn("official instructions", prompt)
        self.assertIn("confirmed", prompt)
        self.assertEqual(prompt.count("TOOL_RESULT ("), 3)
        provider.complete.assert_not_awaited()
