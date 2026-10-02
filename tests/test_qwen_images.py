import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.config import load_config
from wmadapter.providers.router import ProviderRouter
from wmadapter.service import QwenService
from wmadapter.providers.qwen.images import validate_artifact_url, validate_image_bytes


PNG = b"\x89PNG\r\n\x1a\nfixture"


class QwenImageValidationTests(unittest.TestCase):
    def test_accepts_provider_owned_https_artifact(self):
        url = "https://cdn.qwenlm.ai/output/image.png?key=redacted"
        self.assertEqual(validate_artifact_url(url), url)

    def test_rejects_non_provider_artifact_urls(self):
        for url in (
            "http://cdn.qwenlm.ai/output/image.png",
            "https://example.com/image.png",
            "https://cdn.qwenlm.ai.evil.example/image.png",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_artifact_url(url)

    def test_validates_mime_and_magic_bytes(self):
        self.assertEqual(validate_image_bytes(PNG, "image/png"), (PNG, "image/png"))
        with self.assertRaises(ValueError):
            validate_image_bytes(b"not-an-image", "image/png")
        with self.assertRaises(ValueError):
            validate_image_bytes(PNG, "image/jpeg")

    def test_accepts_all_supported_artifact_formats(self):
        fixtures = (
            (b"\xff\xd8\xfffixture", "image/jpeg"),
            (b"GIF89afixture", "image/gif"),
            (b"RIFFxxxxWEBPfixture", "image/webp"),
        )
        for content, mime in fixtures:
            with self.subTest(mime=mime):
                self.assertEqual(validate_image_bytes(content, mime), (content, mime))


class QwenImageContractTests(unittest.TestCase):
    def setUp(self):
        config = load_config('/nonexistent')
        config['qwen'].update({
            'models': ['qwen-chat', 'qwen-image-3.0'],
            'image_generation_verified': True,
            'image_generation_verified_models': ['qwen-chat', 'qwen-image-3.0'],
        })
        self.provider = QwenService(config)
        self.provider.ready = True
        self.patch = patch.object(
            main, 'router', ProviderRouter({'qwen': self.provider}, 'qwen')
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_success_serializes_validated_artifact(self):
        content = b"\x89PNG\r\n\x1a\nfixture"
        self.provider.generate_image = AsyncMock(return_value=(content, 'image/png'))
        response = self.client.post('/v1/images', json={
            'model': 'qwen-chat', 'prompt': 'a fixture', 'response_format': 'b64_json',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'][0]['mime_type'], 'image/png')
        self.assertEqual(response.json()['data'][0]['b64_json'], 'iVBORw0KGgpmaXh0dXJl')

    def test_provider_timeout_is_a_safe_partial_failure(self):
        self.provider.generate_image = AsyncMock(side_effect=TimeoutError())
        response = self.client.post('/v1/images', json={
            'model': 'qwen-chat', 'prompt': 'a fixture',
        })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['error']['code'], 'image_generation_failed')
        self.assertNotIn('TimeoutError', response.text)

    def test_unverified_qwen_image_route_stays_explicitly_unsupported(self):
        self.provider.capabilities = self.provider.capabilities.model_copy(
            update={'image_generation': False}
        )
        response = self.client.post('/v1/images', json={
            'model': 'qwen-chat', 'prompt': 'a fixture',
        })
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()['error']['code'], 'image_generation_unverified')
        self.assertFalse(response.json().get('data'))

    def test_qwen_image_model_maps_aspect_ratio_and_normalizes_response(self):
        content = b"\x89PNG\r\n\x1a\nfixture"
        self.provider.generate_image = AsyncMock(return_value=(content, 'image/png'))
        response = self.client.post('/v1/images', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'aspect_ratio': '16:9',
        })
        self.assertEqual(response.status_code, 200)
        self.provider.generate_image.assert_awaited_once_with(
            'a fixture', model='qwen-image-3.0', size='1280x720',
        )
        self.assertEqual(response.json()['data'][0]['mime_type'], 'image/png')

    def test_models_report_image3_capability_separately(self):
        models = {
            item['id']: item['capabilities']['image_generation']
            for item in self.client.get('/v1/models').json()['data']
        }
        self.assertTrue(models['qwen-chat'])
        self.assertTrue(models['qwen-image-3.0'])

    def test_openai_generations_path_is_served(self):
        # OpenAI-SDK clients post to /v1/images/generations; the same handler
        # backs both paths so no per-client plugin is required.
        content = b"\x89PNG\r\n\x1a\nfixture"
        self.provider.generate_image = AsyncMock(return_value=(content, 'image/png'))
        response = self.client.post('/v1/images/generations', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'aspect_ratio': '16:9',
        })
        self.assertEqual(response.status_code, 200)
        self.provider.generate_image.assert_awaited_once_with(
            'a fixture', model='qwen-image-3.0', size='1280x720',
        )

    def test_camel_case_aspect_ratio_alias_is_accepted(self):
        # OpenClaw's image tool sends aspectRatio; it must reach the same
        # normalized contract and must not be treated as an unsupported field.
        content = b"\x89PNG\r\n\x1a\nfixture"
        self.provider.generate_image = AsyncMock(return_value=(content, 'image/png'))
        response = self.client.post('/v1/images/generations', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'aspectRatio': '9:16',
        })
        self.assertEqual(response.status_code, 200)
        self.provider.generate_image.assert_awaited_once_with(
            'a fixture', model='qwen-image-3.0', size='720x1280',
        )

    def test_both_aspect_spellings_together_are_rejected(self):
        # Ambiguous rather than silently resolved. Body validation failures use
        # the gateway's generic redacted 400 envelope.
        response = self.client.post('/v1/images/generations', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture',
            'aspect_ratio': '1:1', 'aspectRatio': '16:9',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'invalid_request')

    def test_unhonourable_quality_hint_is_still_rejected(self):
        response = self.client.post('/v1/images/generations', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'quality': 'high',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('quality', response.json()['error']['message'])

    def test_qwen_image_rejects_ambiguous_size_and_ratio(self):
        response = self.client.post('/v1/images', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture',
            'size': '1024x1024', 'aspect_ratio': '1:1',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('only one', response.json()['error']['message'].lower())

    def test_qwen_image_rejects_pixel_size_without_a_web_preset(self):
        # 2:3 matches no verified preset shape.
        response = self.client.post('/v1/images', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'size': '1024x1536',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('must match one of', response.json()['error']['message'])

    def test_openai_client_pixel_size_snaps_to_a_preset(self):
        # OpenClaw sends 16:9 as 2048x1152; it must reach the 16:9 preset.
        content = b"\x89PNG\r\n\x1a\nfixture"
        self.provider.generate_image = AsyncMock(return_value=(content, 'image/png'))
        response = self.client.post('/v1/images/generations', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'size': '2048x1152',
        })
        self.assertEqual(response.status_code, 200)
        self.provider.generate_image.assert_awaited_once_with(
            'a fixture', model='qwen-image-3.0', size='1280x720',
        )

    def test_invalid_provider_artifact_is_rejected_at_http_boundary(self):
        self.provider.generate_image = AsyncMock(return_value=(b'not-an-image', 'image/png'))
        response = self.client.post('/v1/images', json={
            'model': 'qwen-image-3.0', 'prompt': 'a fixture', 'size': '1024x1024',
        })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['error']['code'], 'image_generation_failed')


class QwenImageServiceTranslationTests(unittest.IsolatedAsyncioTestCase):
    """Provider translation from the normalized request to Qwen Image 3."""

    def setUp(self):
        config = load_config('/nonexistent')
        config['qwen'].update({
            'models': ['qwen-chat', 'qwen-image-3.0'],
            'image_generation_verified': True,
            'image_generation_verified_models': ['qwen-chat', 'qwen-image-3.0'],
        })
        self.provider = QwenService(config)
        self.provider.ready = True
        self.page = AsyncMock()
        self.provider._page_for_conversation = AsyncMock(return_value=self.page)

    async def test_image3_forwards_normalized_size_to_the_web_driver(self):
        with patch('wmadapter.service.QwenChat') as chat_cls:
            chat_cls.return_value.send_image = AsyncMock(return_value=(PNG, 'image/png'))
            content, mime = await self.provider.generate_image(
                'a fixture', model='qwen-image-3.0', size='1280x720',
            )
        self.assertEqual((content, mime), (PNG, 'image/png'))
        _, kwargs = chat_cls.return_value.send_image.call_args
        self.assertEqual(kwargs, {'size': '1280x720', 'model': 'qwen-image-3.0'})

    async def test_legacy_qwen_chat_keeps_the_default_auto_size(self):
        with patch('wmadapter.service.QwenChat') as chat_cls:
            chat_cls.return_value.send_image = AsyncMock(return_value=(PNG, 'image/png'))
            await self.provider.generate_image('a fixture')
        _, kwargs = chat_cls.return_value.send_image.call_args
        self.assertEqual(kwargs, {})

    async def test_unconfigured_image_model_is_rejected_before_browser_use(self):
        self.provider.model_ids = ('qwen-chat',)
        with patch('wmadapter.service.QwenChat') as chat_cls:
            with self.assertRaises(ValueError):
                await self.provider.generate_image(
                    'a fixture', model='qwen-image-3.0', size='1024x1024')
        chat_cls.assert_not_called()

    async def test_auth_route_page_is_redirected_to_the_chat_root(self):
        # A page parked on the auth route renders no composer, so the image
        # path must move it to the chat root before driving the UI.
        page = AsyncMock()
        page.url = 'https://chat.qwen.ai/auth'
        self.provider._page_for_conversation = AsyncMock(return_value=page)
        await self.provider._chat_ready_page('image:fixture')
        page.goto.assert_awaited_once_with(
            'https://chat.qwen.ai/', wait_until='domcontentloaded')

    async def test_chat_root_derives_from_a_configured_auth_url(self):
        self.provider.chat_url = 'https://chat.qwen.ai/auth'
        self.assertEqual(self.provider._chat_root_url(), 'https://chat.qwen.ai/')
        self.provider.chat_url = 'https://chat.qwen.ai/'
        self.assertEqual(self.provider._chat_root_url(), 'https://chat.qwen.ai/')

    async def test_upstream_rendering_failure_surfaces_a_redacted_error(self):
        with patch('wmadapter.service.QwenChat') as chat_cls:
            chat_cls.return_value.send_image = AsyncMock(
                side_effect=TimeoutError('Qwen image artifact was not rendered before timeout'))
            with self.assertRaises(TimeoutError):
                await self.provider.generate_image(
                    'a fixture', model='qwen-image-3.0', size='1024x1024')


if __name__ == "__main__":
    unittest.main()
