import unittest
from unittest.mock import AsyncMock, Mock

from wmadapter.providers.contract import ChatRequest, Message, ModelCapabilities, ProviderRequest, canonicalize
from wmadapter.service import DeepSeekService, QwenService
from wmadapter.config import load_config
from wmadapter.api.validation import validate_chat

TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]


class ContractTests(unittest.IsolatedAsyncioTestCase):
    IMAGE_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'

    async def test_canonical_request_is_provider_independent(self):
        request = ChatRequest(model='deepseek-chat', messages=[
            Message(role='assistant', content=None, tool_calls=[{'id':'c1','type':'function','function':{'name':'x','arguments':'{}'}}]),
            Message(role='tool', content='ok', tool_call_id='c1'),
        ])
        canonical = canonicalize(request)
        self.assertEqual(canonical.messages[0].role, 'assistant')
        self.assertEqual(canonical.messages[1].tool_call_id, 'c1')
        self.assertEqual(canonical.tools, [])

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

    async def test_qwen_v2_stream_and_legacy(self):
        provider = QwenService(load_config('/nonexistent'))
        provider.complete = AsyncMock(return_value='qwen answer')
        request = ProviderRequest(chat=ChatRequest(messages=[Message(role='user', content='hi')]))
        chunks = [chunk async for chunk in provider.stream_infer(request)]
        self.assertEqual(chunks[0].content, 'qwen answer')
        self.assertEqual(await provider.complete('old prompt'), 'qwen answer')
        self.assertFalse(provider.capabilities.image_input)

    async def test_qwen_multimodal_parts_are_rejected_before_provider_submission(self):
        provider = QwenService(load_config('/nonexistent'))
        for part_type, part in (
            ('image', {'type': 'image_url', 'image_url': {'url': self.IMAGE_DATA_URL}}),
            ('video', {'type': 'input_video', 'video': {'data': 'x'}}),
            ('audio', {'type': 'input_audio', 'input_audio': {'data': 'x', 'format': 'wav'}}),
            ('file', {'type': 'input_file', 'file': {'filename': 'secret.pdf', 'file_data': 'x'}}),
            ('pdf', {'type': 'pdf', 'file': {'filename': 'secret.pdf'}}),
        ):
            with self.subTest(part_type=part_type):
                with self.assertRaisesRegex(Exception, 'Unsupported audio or video|not currently supported|Unsupported file or PDF'):
                    validate_chat(ChatRequest(model='qwen-chat', messages=[Message(role='user', content=[part])]), provider)

    async def test_canonical_request_preserves_parts_without_provider_specific_rewrite(self):
        content = [
            {'type': 'text', 'text': 'describe this'},
            {'type': 'image_url', 'image_url': {'url': self.IMAGE_DATA_URL}},
        ]
        request = ChatRequest(messages=[Message(role='user', content=content)])
        self.assertEqual(canonicalize(request).messages[0].content, content)

    async def test_verified_image_input_rejects_oversized_data_before_submission(self):
        provider = QwenService(load_config('/nonexistent'))
        provider.capabilities = ModelCapabilities(image_input=True)
        oversized = 'data:image/png;base64,' + ('A' * (14 * 1024 * 1024))
        with self.assertRaisesRegex(Exception, '10 MiB'):
            validate_chat(ChatRequest(messages=[Message(role='user', content=[
                {'type': 'image_url', 'image_url': {'url': oversized}}
            ])]), provider)

    async def test_qwen_error_observability_is_sanitized(self):
        provider = QwenService(load_config('/nonexistent'))
        error = RuntimeError('provider response secret.pdf rendered text')
        self.assertEqual(provider._safe_error(error), 'provider_request_failed:RuntimeError')

    async def test_qwen_model_ids_are_explicit_and_deterministic(self):
        base = load_config('/nonexistent')
        base['qwen'] = {'models': ['qwen-text', 'qwen-agent']}
        provider = QwenService(base)
        self.assertEqual(provider.model_ids, ('qwen-text', 'qwen-agent'))
        base['qwen'] = {'models': ['qwen-text', 'qwen-text']}
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            QwenService(base)

    async def test_qwen_preserves_authenticated_page(self):
        from unittest.mock import patch
        provider = QwenService(load_config('/nonexistent'))
        page = AsyncMock()
        provider._page_for_conversation = AsyncMock(return_value=page)
        with patch('wmadapter.service.QwenChat.probe_auth', new=AsyncMock(return_value='CHAT_READY')):
            await provider._authenticate('session')
        page.goto.assert_not_called()
        self.assertTrue(provider.ready)

    async def test_qwen_probe_rejects_logged_out_composer(self):
        from wmadapter.providers.qwen.chat import QwenChat

        class Control:
            async def is_visible(self, timeout=0):
                return True

            async def inner_text(self):
                return "Log in"

        class Controls:
            async def count(self):
                return 1

            def nth(self, index):
                return Control()

        page = Mock()
        page.locator = Mock(return_value=Controls())
        state = await QwenChat(page).probe_auth()
        self.assertEqual(state, "SIGN_IN_VISIBLE")

    async def test_deepseek_image_dispatch(self):
        provider = DeepSeekService(load_config('/nonexistent'))
        provider.capabilities = ModelCapabilities(image_input=True)
        provider.complete_with_attachments = AsyncMock(return_value='image answer')
        request = ProviderRequest(chat=ChatRequest(messages=[Message(role='user', content=[
            {'type':'image_url','image_url':{'url':self.IMAGE_DATA_URL}}
        ])]))
        result = await provider.infer(request)
        self.assertEqual(result.content, 'image answer')
        self.assertEqual(provider.complete_with_attachments.call_args.kwargs['attachments'], [self.IMAGE_DATA_URL])

    async def test_deepseek_image_capability_is_not_advertised_without_live_verification(self):
        provider = DeepSeekService(load_config('/nonexistent'))
        self.assertFalse(provider.capabilities.image_input)

    async def test_deepseek_rejects_malformed_image_bytes_before_browser_upload(self):
        from wmadapter.providers.deepseek.chat import _decode_image_data_url

        with self.assertRaisesRegex(ValueError, 'does not match'):
            _decode_image_data_url('data:image/png;base64,aGVsbG8=')

    async def test_deepseek_upload_control_failure_is_pre_submit_and_safe(self):
        from tempfile import TemporaryDirectory
        from wmadapter.providers.deepseek.chat import DeepSeekChat
        from wmadapter.providers.submit import PreSubmitError

        page = AsyncMock()
        file_input = AsyncMock()
        file_input.count.return_value = 1
        file_input.set_input_files.side_effect = RuntimeError('browser detail')
        page.locator = Mock(return_value=Mock(last=file_input))
        chat = DeepSeekChat(page)
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PreSubmitError, 'upload control is unavailable'):
                await chat._attach_data_images([self.IMAGE_DATA_URL], directory)
        file_input.set_input_files.assert_awaited_once()

    async def test_partial_qwen_timeout_is_not_success(self):
        from unittest.mock import patch, Mock
        from wmadapter.providers.qwen.chat import QwenChat
        page = AsyncMock()
        chat = QwenChat(page, timeout_ms=1000)
        chat._first_visible = AsyncMock(return_value=AsyncMock())
        chat._response_counts = AsyncMock(return_value={})
        chat._latest_response_text = AsyncMock(return_value='partial')
        clock = Mock()
        clock.time.side_effect = [0, 0, 2]
        with patch('wmadapter.providers.qwen.chat.asyncio.get_running_loop', return_value=clock), patch('wmadapter.providers.qwen.chat.asyncio.sleep', new=AsyncMock()):
            from wmadapter.providers.submit import SubmitState, UncertainSubmitError
            with self.assertRaises(UncertainSubmitError):
                await chat.send_message('hi')
            self.assertEqual(chat.submit_state, SubmitState.SUBMITTED_UNCERTAIN)

    async def test_partial_deepseek_timeout_is_not_success(self):
        from unittest.mock import patch, Mock
        from wmadapter.providers.deepseek.chat import DeepSeekChat
        page = AsyncMock()
        chat = DeepSeekChat(page, timeout_ms=1000)
        chat._first_visible = AsyncMock(return_value=AsyncMock())
        chat._enabled_send_button = AsyncMock(return_value=None)
        blocks = AsyncMock()
        blocks.count.side_effect = [0, 1]
        chat._response_locator = AsyncMock(return_value=blocks)
        chat._response_text = AsyncMock(return_value='partial')
        clock = Mock()
        clock.time.side_effect = [0, 0, 2]
        with patch('wmadapter.providers.deepseek.chat.asyncio.get_running_loop', return_value=clock), patch('wmadapter.providers.deepseek.chat.asyncio.sleep', new=AsyncMock()):
            from wmadapter.providers.submit import SubmitState, UncertainSubmitError
            with self.assertRaises(UncertainSubmitError):
                await chat.send_message('hi')
            self.assertEqual(chat.submit_state, SubmitState.SUBMITTED_UNCERTAIN)

    async def test_deepseek_reconciles_late_response_without_resubmitting(self):
        from unittest.mock import patch, Mock
        from wmadapter.providers.deepseek.chat import DeepSeekChat
        from wmadapter.providers.submit import SubmitState

        chat = DeepSeekChat(AsyncMock())
        chat.submit_state = SubmitState.SUBMITTED_UNCERTAIN
        chat._previous_response_count = 0
        chat._previous_response_text = ''
        blocks = AsyncMock()
        blocks.count.return_value = 1
        chat._response_locator = AsyncMock(return_value=blocks)
        chat._response_text = AsyncMock(return_value='late answer')
        clock = Mock()
        clock.time.side_effect = [0, 0, 0, 0, 0, 0, 0]
        with patch('wmadapter.providers.deepseek.chat.asyncio.get_running_loop', return_value=clock), \
             patch('wmadapter.providers.deepseek.chat.asyncio.sleep', new=AsyncMock()):
            answer = await chat.recover_response(1000)

        self.assertEqual(answer, 'late answer')
        self.assertEqual(chat.submit_state, SubmitState.COMPLETED)
        chat._response_locator.assert_awaited()

    async def test_qwen_service_does_not_retry_uncertain_submission(self):
        from unittest.mock import patch, AsyncMock
        from wmadapter.providers.qwen.chat import QwenChat
        from wmadapter.providers.submit import UncertainSubmitError
        from wmadapter.service import QwenService

        provider = QwenService(load_config('/nonexistent'))
        provider._authenticate = AsyncMock()
        provider._page_for_conversation = AsyncMock(return_value=AsyncMock())
        provider.browser.restart = AsyncMock()
        chat = QwenChat(AsyncMock())
        chat.send_message = AsyncMock(side_effect=UncertainSubmitError())
        with patch('wmadapter.service.QwenChat', return_value=chat):
            with self.assertRaises(UncertainSubmitError):
                await provider.complete('hello')
        chat.send_message.assert_awaited_once_with('hello')
        provider.browser.restart.assert_not_awaited()

    async def test_deepseek_attachment_uncertain_submission_is_not_retried(self):
        from unittest.mock import patch, AsyncMock
        from wmadapter.providers.deepseek.chat import DeepSeekChat
        from wmadapter.providers.submit import UncertainSubmitError
        from wmadapter.service import DeepSeekService

        provider = DeepSeekService(load_config('/nonexistent'))
        provider._authenticate = AsyncMock()
        provider._page_for_conversation = AsyncMock(return_value=AsyncMock())
        provider.browser.restart = AsyncMock()
        chat = DeepSeekChat(AsyncMock())
        chat.send_message = AsyncMock(side_effect=UncertainSubmitError())
        with patch('wmadapter.service.DeepSeekChat', return_value=chat):
            with self.assertRaises(UncertainSubmitError):
                await provider.complete_with_attachments('hello', attachments=['data:image/png;base64,aA=='])
        chat.send_message.assert_awaited_once_with('hello', attachments=['data:image/png;base64,aA=='])
        provider.browser.restart.assert_not_awaited()

    def test_deepseek_timeout_scales_for_long_opencode_prompts(self):
        from wmadapter.service import DeepSeekService
        provider = DeepSeekService(load_config('/nonexistent'))
        self.assertEqual(provider._request_timeout_ms('x' * 12000), provider.timeout_ms)
        self.assertGreater(provider._request_timeout_ms('x' * 45000), provider.timeout_ms)
        self.assertLessEqual(provider._request_timeout_ms('x' * 1_000_000), 900000)
