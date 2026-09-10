import unittest
from unittest.mock import AsyncMock

from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import ChatRequest, Message, ProviderRequest
from wmadapter.providers.errors import ContextLimitError
from wmadapter.providers.protocol import compact_messages, minimize_tool_schemas


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
        with self.assertRaises(ContextLimitError):
            await provider.infer(request)
        provider.complete.assert_not_awaited()

    def test_tool_schema_minimization_preserves_callable_fields(self):
        tool = {"type": "function", "function": {
            "name": "lookup", "description": "find", "parameters": {"type": "object"},
            "strict": True, "provider_private": "omit",
        }}
        self.assertEqual(minimize_tool_schemas([tool]), [{"type": "function", "function": {
            "name": "lookup", "description": "find", "parameters": {"type": "object"}, "strict": True,
        }}])


if __name__ == "__main__":
    unittest.main()
