import unittest
from unittest.mock import AsyncMock

from webbridgefreeride.providers.contract import ChatRequest, Message, ProviderRequest
from webbridgefreeride.service import DeepSeekService, QwenService
from webbridgefreeride.config import load_config

TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_v2_and_legacy(self):
        provider = DeepSeekService(load_config('/nonexistent'))
        provider.complete = AsyncMock(return_value='hello')
        result = await provider.infer(ProviderRequest(chat=ChatRequest(messages=[Message(role='user', content='hi')]), conversation_id='session'))
        self.assertEqual(result.content, 'hello')
        self.assertEqual(result.finish_reason, 'stop')
        self.assertEqual(provider.complete.call_args.kwargs['conversation_id'], 'session')
        self.assertEqual(await provider.complete('old prompt'), 'hello')

    async def test_deepseek_tool_loop(self):
        provider = DeepSeekService(load_config('/nonexistent'))
        provider.complete = AsyncMock(side_effect=['<tool_call>{"name":"lookup","arguments":{}}</tool_call>', 'found'])
        messages = [Message(role='user', content='lookup value')]
        first = await provider.infer(ProviderRequest(chat=ChatRequest(messages=messages, tools=TOOLS)))
        self.assertEqual(first.finish_reason, 'tool_calls')
        call = first.tool_calls[0]
        messages += [Message(role='assistant', content='checking', tool_calls=[call]), Message(role='tool', tool_call_id=call['id'], content='42')]
        second = await provider.infer(ProviderRequest(chat=ChatRequest(messages=messages, tools=TOOLS)))
        self.assertEqual(second.content, 'found')
        prompt = provider.complete.call_args.args[0]
        self.assertIn(call['id'], prompt)
        self.assertIn('ASSISTANT: checking', prompt)
        self.assertIn('42', prompt)

    async def test_required_choice_fails_without_call(self):
        provider = DeepSeekService(load_config('/nonexistent'))
        provider.complete = AsyncMock(return_value='ordinary answer')
        with self.assertRaisesRegex(ValueError, 'tool_choice'):
            await provider.infer(ProviderRequest(chat=ChatRequest(messages=[Message(role='user', content='hi')], tools=TOOLS, tool_choice='required')))
