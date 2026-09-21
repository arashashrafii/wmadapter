import unittest
from unittest.mock import AsyncMock

from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import ChatRequest, Message, ProviderRequest
from wmadapter.providers.errors import ContextLimitError
from wmadapter.providers.protocol import bound_tool_results, compact_messages, minimize_tool_schemas
from wmadapter.providers.policy import ClientPolicy


class BudgetProvider(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat",)

    async def start(self): pass
    async def stop(self): pass
    async def status(self): return {"ready": True}
    async def complete(self, prompt, conversation_id=None): return "ok"


class ContextBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_small_prompt_is_unchanged_and_metrics_are_sanitized(self):
        provider = BudgetProvider()
        provider.complete = AsyncMock(return_value="ok")
        request = ProviderRequest(chat=ChatRequest(messages=[Message(role="user", content="hello")]))
        with self.assertLogs("wmadapter.providers.legacy", level="INFO") as logs:
            await provider.infer(request)
        prompt = provider.complete.call_args.args[0]
        self.assertIn("USER: hello", prompt)
        self.assertNotIn("COMPACTED STATE LEDGER", prompt)
        self.assertIn("provider_request_metrics", logs.output[0])
        self.assertIn("prompt_length=", logs.output[0])
        self.assertNotIn("USER: hello", logs.output[0])

    async def test_large_history_keeps_recent_window_and_state_ledger(self):
        messages = [Message(role="user", content=f"old-{index}-" + "x" * 500) for index in range(10)]
        messages.append(Message(role="user", content="latest"))
        compacted = compact_messages(messages, 1800)
        self.assertEqual(compacted[-1].content, "latest")
        self.assertIn("COMPACTED STATE LEDGER", compacted[0].content)
        self.assertNotIn("old-0-", compacted[0].content)

    async def test_compaction_submits_once_and_uses_model_profile(self):
        provider = BudgetProvider()
        provider.context_budget_chars = 9000
        provider.context_budget_profiles = {"deepseek-chat": 2200}
        provider.complete = AsyncMock(return_value="ok")
        messages = [Message(role="user", content=f"old-{index}-" + "x" * 500) for index in range(5)]
        messages.append(Message(role="user", content="latest"))
        request = ProviderRequest(chat=ChatRequest(model="deepseek-chat", messages=messages))
        await provider.infer(request)
        prompt = provider.complete.call_args.args[0]
        self.assertIn("COMPACTED STATE LEDGER", prompt)
        self.assertIn("USER: latest", prompt)
        provider.complete.assert_awaited_once()

    async def test_oversized_current_message_fails_before_completion(self):
        provider = BudgetProvider()
        provider.context_budget_chars = 2000
        provider.complete = AsyncMock(return_value="must not be called")
        request = ProviderRequest(chat=ChatRequest(messages=[Message(role="user", content="x" * 10000)]))
        with self.assertRaisesRegex(ContextLimitError, r"prompt_length=\d+, budget=2000"):
            await provider.infer(request)
        provider.complete.assert_not_awaited()

    def test_browser_relay_tool_result_keeps_bounded_excerpt_and_fingerprint(self):
        result = "BEGIN profile facts\n" + ("middle-noise " * 5000) + "\nEND relay status"
        message = Message(role="tool", tool_call_id="browser-1", content=result)

        bounded = bound_tool_results([message], 1800)[0]

        self.assertEqual(bounded.tool_call_id, "browser-1")
        self.assertLessEqual(len(bounded.content), 1800)
        self.assertIn("BEGIN profile facts", bounded.content)
        self.assertIn("END relay status", bounded.content)
        self.assertIn("[TOOL RESULT COMPACTED:", bounded.content)
        self.assertIn("original_chars=", bounded.content)
        self.assertNotIn("middle-noise " * 100, bounded.content)

    async def test_oversized_browser_relay_result_is_submitted_within_budget(self):
        provider = BudgetProvider()
        provider.context_budget_chars = 12000
        provider.complete = AsyncMock(return_value="ok")
        messages = [
            Message(role="user", content="Continue the Instagram profile audit."),
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": "browser-1", "type": "function", "function": {
                    "name": "browser", "arguments": "{\"action\":\"snapshot\"}"
                }}],
            ),
            Message(
                role="tool",
                tool_call_id="browser-1",
                content="BEGIN snapshot\n" + ("relay-output " * 20000) + "\nEND snapshot",
            ),
        ]
        request = ProviderRequest(chat=ChatRequest(model="deepseek-chat", messages=messages, tools=[{
            "type": "function", "function": {"name": "browser", "description": "Use the browser relay.",
                                                    "parameters": {"type": "object"}},
        }]))

        await provider.infer(request)

        prompt = provider.complete.call_args.args[0]
        self.assertLessEqual(len(prompt), 12000)
        self.assertIn("[TOOL RESULT COMPACTED:", prompt)
        provider.complete.assert_awaited_once()

    def test_tool_schema_minimization_preserves_callable_fields(self):
        tool = {"type": "function", "function": {
            "name": "lookup", "description": "find", "parameters": {"type": "object"},
            "strict": True, "provider_private": "omit",
        }}
        self.assertEqual(minimize_tool_schemas([tool]), [{"type": "function", "function": {
            "name": "lookup", "description": "find", "parameters": {"type": "object"}, "strict": True,
        }}])

    def test_opencode_tool_schema_compaction_preserves_callable_shape(self):
        tool = {"type": "function", "function": {
            "name": "write", "description": "x" * 2000,
            "parameters": {"type": "object", "description": "omit", "properties": {
                "path": {"type": "string", "description": "omit"},
            }},
        }}
        compacted = minimize_tool_schemas([tool], compact_descriptions=True)[0]["function"]
        self.assertEqual(compacted["name"], "write")
        self.assertEqual(compacted["parameters"], {"type": "object", "properties": {"path": {"type": "string"}}})
        self.assertLessEqual(len(compacted["description"]), 190)

    async def test_openclaw_context_budget_drops_verbose_policy_before_rejecting_request(self):
        provider = BudgetProvider()
        provider.complete = AsyncMock(return_value="hello")
        request = ProviderRequest(
            chat=ChatRequest(model="deepseek-chat", messages=[Message(role="user", content="open browser")], tools=[{
                "type": "function", "function": {"name": "computer", "description": "x" * 2000,
                "parameters": {"type": "object", "properties": {"action": {"type": "string"}}}},
            }]),
            conversation_id="test", client_policy=ClientPolicy.OPENCLAW, context_budget_chars=3000,
        )

        await provider.infer(request)

        prompt = provider.complete.call_args.args[0]
        self.assertLessEqual(len(prompt), 3000)
        self.assertNotIn("OPENCLAW DOCUMENTATION POLICY", prompt)


if __name__ == "__main__":
    unittest.main()
