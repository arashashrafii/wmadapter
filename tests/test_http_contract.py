import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from mimicgate import main
from mimicgate.api.server import app as alternate_app
from mimicgate.providers.base import ChatProvider
from mimicgate.providers.router import ProviderRouter
from mimicgate.service import PageCapacityError
from test_contract_v2 import TOOLS


class FakeProvider(ChatProvider):
    name = 'deepseek'
    model_ids = ('deepseek-chat', 'deepseek-reasoner')

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {'ready': True}

    async def complete(self, prompt, conversation_id=None):
        return 'hello'


class HTTPContractTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.provider = FakeProvider()
        self.provider.complete = AsyncMock(return_value='hello')
        self.patch = patch.object(main, 'router', ProviderRouter({'deepseek': self.provider}, 'deepseek'))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def post(self, **kwargs):
        return self.client.post('/v1/chat/completions', json={
            'model': 'deepseek-chat', 'messages': [{'role': 'user', 'content': 'hi'}], **kwargs})

    def test_entrypoints_and_models(self):
        self.assertIs(main.app, alternate_app)
        models = self.client.get('/v1/models').json()['data']
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]['capabilities']['streaming'], 'buffered')
        self.assertIsNone(models[0]['capabilities']['context_window'])

    def test_completion(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['choices'][0]['message']['content'], 'hello')
        self.assertEqual(body['choices'][0]['finish_reason'], 'stop')
        self.assertIsNone(body['usage'])

    def test_not_ready_is_a_stable_safe_provider_error(self):
        self.provider.ready = False
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'provider_not_ready')
        self.assertNotIn('secret', response.text)
        self.provider.complete.assert_not_called()

    def test_openclaw_shaped_healthy_request_reaches_inference(self):
        self.provider.ready = True
        response = self.client.post('/v1/chat/completions', json={
            'model': 'mimicgate/deepseek-chat',
            'messages': [{'role': 'user', 'content': 'hello from OpenClaw'}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['choices'][0]['message']['content'], 'hello')
        self.provider.complete.assert_awaited_once()

    def test_page_capacity_is_separate_and_actionable(self):
        self.provider.ready = True
        self.provider.infer = AsyncMock(side_effect=PageCapacityError('internal page count'))
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'provider_capacity')
        self.assertIn('idle conversation', response.json()['error']['message'])
        self.assertNotIn('internal page count', response.text)

    def test_streaming_page_capacity_uses_safe_error_code(self):
        self.provider.ready = True
        self.provider.infer = AsyncMock(side_effect=PageCapacityError('internal page count'))
        response = self.post(stream=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('"code":"provider_capacity"', response.text)
        self.assertNotIn('internal page count', response.text)
        self.assertTrue(response.text.endswith('data: [DONE]\n\n'))

    def test_client_shaped_tool_roundtrips(self):
        # Common wire subset, not execution of the three real clients.
        for client in ['OpenCode', 'Hermes', 'OpenClaw']:
            with self.subTest(client=client):
                self.provider.complete = AsyncMock(side_effect=[
                    '<tool_call>{"name":"lookup","arguments":{}}</tool_call>', '42'])
                first = self.post(tools=TOOLS, tool_choice='auto').json()
                message = first['choices'][0]['message']
                self.assertEqual(first['choices'][0]['finish_reason'], 'tool_calls')
                call = message['tool_calls'][0]
                self.assertEqual(json.loads(call['function']['arguments']), {})
                second = self.post(tools=TOOLS, messages=[{'role':'user','content':'hi'}, message,
                    {'role':'tool', 'tool_call_id':call['id'], 'content':'42'}])
                self.assertEqual(second.json()['choices'][0]['message']['content'], '42')
                self.assertIn(call['id'], self.provider.complete.call_args.args[0])

    def test_sse_text_and_tool(self):
        for answer, finish in [('hello', 'stop'), ('<tool_call>{"name":"lookup","arguments":{}}</tool_call>', 'tool_calls')]:
            self.provider.complete = AsyncMock(return_value=answer)
            response = self.post(stream=True, tools=TOOLS, stream_options={'include_usage': True})
            self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
            frames = [line[6:] for line in response.text.splitlines() if line.startswith('data: ')]
            self.assertEqual(frames[-1], '[DONE]')
            chunks = [json.loads(line) for line in frames[:-1]]
            self.assertEqual(chunks[0]['choices'][0]['delta']['role'], 'assistant')
            self.assertEqual(chunks[-2]['choices'][0]['finish_reason'], finish)
            self.assertEqual(chunks[-1]['choices'], [])
            if finish == 'tool_calls':
                self.assertEqual(chunks[1]['choices'][0]['delta']['tool_calls'][0]['index'], 0)

    def test_invalid_requests_do_not_reach_provider(self):
        for overrides in [
            {'messages': []}, {'messages':[{'role':'bogus','content':'hi'}]},
            {'messages':[{'role':'tool','tool_call_id':'missing','content':'hi'}]},
            {'tools':[{'type':'function','function':{}}]},
            {'tool_choice':'required'}, {'tool_choice':{'type':'function','function':{'name':'missing'}}},
            {'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://example.com/a.png'}}]}]},
            {'n':2}, {'stream_options': 'invalid'},
            {'tool_choice': {'type':'function','function':{'name':[]}}},
        ]:
            with self.subTest(overrides=overrides):
                response = self.post(**overrides)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['error']['type'], 'invalid_request_error')
        self.provider.complete.assert_not_called()

    def test_unknown_model(self):
        response = self.post(model='gpt-invented')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'model_not_found')
        self.provider.complete.assert_not_called()

    def test_optional_bearer_auth(self):
        with patch.object(main, 'gateway_api_key', 'secret'):
            unauthorized = self.client.get('/v1/models')
            authorized = self.client.get('/v1/models', headers={'Authorization':'Bearer secret'})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)

    def test_provider_error_and_stream_error(self):
        self.provider.complete = AsyncMock(side_effect=RuntimeError('secret internal detail'))
        self.assertEqual(self.post().status_code, 502)
        response = self.post(stream=True)
        self.assertIn('provider_error', response.text)
        self.assertNotIn('secret internal detail', response.text)
        self.assertTrue(response.text.endswith('data: [DONE]\n\n'))

    def test_named_and_required_choice(self):
        self.provider.complete = AsyncMock(return_value='ordinary answer')
        self.assertEqual(self.post(tools=TOOLS, tool_choice='required').status_code, 502)
        self.provider.complete = AsyncMock(return_value='<tool_call>{"name":"lookup","arguments":{}}</tool_call>')
        response = self.post(tools=TOOLS, tool_choice={'type':'function','function':{'name':'lookup'}})
        self.assertEqual(response.json()['choices'][0]['finish_reason'], 'tool_calls')

    def test_none_disables_tool_calls(self):
        self.provider.complete = AsyncMock(return_value='<tool_call>{"name":"lookup","arguments":{}}</tool_call>')
        self.assertEqual(self.post(tools=TOOLS, tool_choice='none').json()['choices'][0]['finish_reason'], 'stop')

    def test_openclaw_words_do_not_change_public_gateway_policy(self):
        self.provider.complete = AsyncMock(return_value='hello')
        response = self.client.post('/v1/chat/completions', json={
            'model': 'deepseek-chat', 'tools': TOOLS,
            'messages': [{'role': 'user', 'content': 'OpenClaw should use computer.act'}],
        })
        self.assertEqual(response.status_code, 200)
        prompt = self.provider.complete.call_args.args[0]
        self.assertNotIn('OPENCLAW CAPABILITY POLICY', prompt)
        self.assertNotIn('OPENCLAW DOCUMENTATION POLICY', prompt)
        self.assertNotIn('DESKTOP GUI POLICY', prompt)

    def test_title_honors_sse(self):
        response = self.post(stream=True, messages=[{'role':'system','content':'Generate a concise session title'}, {'role':'user','content':'my title'}])
        self.assertIn('data: [DONE]', response.text)
        self.provider.complete.assert_not_called()

    def test_malformed_json_and_unknown_route(self):
        response = self.client.post('/v1/chat/completions', content='{bad', headers={'content-type':'application/json'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assertIn('error', self.client.get('/v1/missing').json())

    def test_registry_extension_and_session_header(self):
        self.provider.model_ids += ('future-tested-model',)
        response = self.client.post('/v1/chat/completions', json={
            'model':'future-tested-model', 'messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.provider.complete.call_args.kwargs['conversation_id'].startswith('auto:'))
