import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.config import load_config
from wmadapter.providers.router import ProviderRouter
from wmadapter.service import QwenService


class QwenMediaBoundaryTests(unittest.TestCase):
    def setUp(self):
        provider = QwenService(load_config('/nonexistent'))
        provider.capabilities = provider.capabilities.model_copy(update={
            'image_generation': False,
            'video_generation': False,
        })
        self.patch = patch.object(main, 'router', ProviderRouter({'qwen': provider}, 'qwen'))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.fixture = json.loads((Path(__file__).parent / 'fixtures' / 'qwen_multimodal_workflows.json').read_text())

    def test_qwen_media_fixture_is_explicit_and_redacted(self):
        self.assertFalse(any(self.fixture['verified'][name] for name in (
            'image_understanding', 'image_editing', 'audio_understanding',
            'speech_input', 'speech_output', 'voice_conversation',
            'video_understanding', 'long_video_understanding',
            'availability_quota_model_region_size_limits',
        )))
        self.assertIn('credentials', self.fixture['redaction'])

    def test_qwen_models_report_all_unverified_media_capabilities(self):
        capabilities = self.client.get('/v1/models').json()['data'][0]['capabilities']
        for name in ('image_input', 'image_editing', 'video_input', 'audio_input',
                     'audio_output', 'realtime'):
            self.assertFalse(capabilities[name], name)

    def test_image_editing_validates_then_reports_unsupported(self):
        response = self.client.post('/v1/images/edits', json={
            'model': 'qwen-chat', 'image': 'fixture-image', 'prompt': 'make it blue',
        })
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()['error']['code'], 'image_editing_not_supported')
        invalid = self.client.post('/v1/images/edits', json={
            'model': 'qwen-chat', 'image': 'fixture-image', 'prompt': 'make it blue', 'unknown': True,
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['error']['code'], 'unsupported_feature')

    def test_video_contract_validates_then_reports_unsupported(self):
        response = self.client.post('/v1/videos', json={
            'model': 'qwen-chat', 'prompt': 'fixture video',
        })
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()['error']['code'], 'video_generation_not_supported')

        invalid = self.client.post('/v1/videos', json={
            'model': 'qwen-chat', 'prompt': 'fixture video', 'unknown': True,
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['error']['code'], 'unsupported_feature')

    def test_video_status_content_and_delete_never_fabricate_state(self):
        for response in (
            self.client.get('/v1/videos/fixture-job'),
            self.client.get('/v1/videos/fixture-job/content'),
            self.client.delete('/v1/videos/fixture-job'),
        ):
            self.assertEqual(response.status_code, 501)
            self.assertEqual(response.json()['error']['code'], 'video_generation_not_supported')

    def test_qwen_capability_is_separate_from_deepseek(self):
        provider = QwenService(load_config('/nonexistent'))
        provider.capabilities = provider.capabilities.model_copy(update={'image_generation': True})
        self.assertTrue(provider.capabilities.image_generation)
        from wmadapter.service import DeepSeekService
        deepseek = DeepSeekService(load_config('/nonexistent'))
        self.assertFalse(deepseek.capabilities.image_generation)

    def test_qwen_models_truthfully_advertise_unverified_media_as_disabled(self):
        capabilities = self.client.get('/v1/models').json()['data'][0]['capabilities']
        for capability in ('audio_input', 'audio_output', 'realtime', 'image_input',
                           'video_input', 'video_generation', 'file_input', 'pdf_input'):
            with self.subTest(capability=capability):
                self.assertFalse(capabilities[capability])
        self.assertEqual(capabilities['usage_reporting'], 'unavailable')

    def test_audio_speech_transcription_translation_and_voice_session_are_independent(self):
        cases = (
            ('/v1/audio/speech', {'model': 'qwen-chat', 'input': 'fixture speech', 'voice': 'fixture'}),
            ('/v1/audio/transcriptions', {'model': 'qwen-chat', 'input': 'fixture-audio-bytes'}),
            ('/v1/audio/translations', {'model': 'qwen-chat', 'input': 'fixture-audio-bytes'}),
            ('/v1/realtime', {'model': 'qwen-chat', 'modalities': ['audio']}),
        )
        for path, body in cases:
            with self.subTest(path=path):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 501)
                self.assertIn(response.json()['error']['code'], {
                    'audio_not_supported', 'realtime_not_supported',
                })

    def test_image_editing_and_image_or_long_video_understanding_are_not_claimed(self):
        parts = (
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB'}},
            {'type': 'input_video', 'video': {'data': 'fixture-long-video'}},
            {'type': 'input_audio', 'input_audio': {'data': 'fixture-audio', 'format': 'wav'}},
        )
        for part in parts:
            response = self.client.post('/v1/chat/completions', json={
                'model': 'qwen-chat', 'messages': [{'role': 'user', 'content': [part]}],
            })
            self.assertEqual(response.status_code, 400)
            self.assertIn(response.json()['error']['code'], {'unsupported_feature', 'invalid_request_error'})


if __name__ == '__main__':
    unittest.main()
