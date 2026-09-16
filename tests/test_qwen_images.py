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
        self.provider = QwenService(load_config('/nonexistent'))
        self.provider.capabilities = self.provider.capabilities.model_copy(
            update={'image_generation': True}
        )
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


if __name__ == "__main__":
    unittest.main()
