import unittest
from unittest.mock import AsyncMock, Mock, patch

from wmadapter.config import load_config
from wmadapter.providers.retry import FailureClass, RecoveryPolicy, classify_failure, recovery_context
from wmadapter.providers.submit import PreSubmitError, SubmitState, UncertainSubmitError
from wmadapter.service import DeepSeekService, QwenService


class RecoveryPolicyTests(unittest.TestCase):
    def test_classification_is_terminal_for_auth_rate_limit_and_protocol(self):
        self.assertEqual(classify_failure(RuntimeError("challenge_visible")), FailureClass.AUTHENTICATION)
        self.assertEqual(classify_failure(RuntimeError("rate limit")), FailureClass.RATE_LIMIT)
        self.assertEqual(classify_failure(ValueError("invalid provider payload")), FailureClass.PROTOCOL)
        self.assertEqual(classify_failure(PreSubmitError("before send")), FailureClass.PRE_SUBMIT)
        self.assertEqual(classify_failure(UncertainSubmitError()), FailureClass.SUBMITTED_UNOBSERVED)

    def test_policy_never_replays_ambiguous_or_tool_bearing_submissions(self):
        policy = RecoveryPolicy()
        self.assertTrue(policy.can_retry(FailureClass.PRE_SUBMIT, has_tools_or_side_effects=True))
        self.assertFalse(policy.can_retry(FailureClass.SUBMITTED_UNOBSERVED))
        self.assertFalse(policy.can_retry(FailureClass.SUBMITTED_UNOBSERVED, has_tools_or_side_effects=False))
        resend = RecoveryPolicy(allow_resend=True)
        self.assertTrue(resend.can_retry(FailureClass.SUBMITTED_UNOBSERVED, has_tools_or_side_effects=False))
        self.assertFalse(resend.can_retry(FailureClass.SUBMITTED_UNOBSERVED, has_tools_or_side_effects=True))


class ProviderRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _config(self, name: str) -> dict:
        config = load_config("/nonexistent")
        config[name].update({
            "recovery_enabled": True,
            "recovery_max_attempts": 3,
            "recovery_backoff_base_ms": 0,
            "recovery_backoff_max_ms": 0,
            "recovery_deadline_ms": 1000,
            "recovery_timeout_ms": 1000,
        })
        return config

    async def test_deepseek_retries_only_pre_submit_failure_and_redacts_logs(self):
        provider = DeepSeekService(self._config("deepseek"))
        page = AsyncMock()
        provider._page_for_conversation = AsyncMock(return_value=page)
        provider._authenticate = AsyncMock()
        provider.browser.restart = AsyncMock()
        chat = Mock()
        chat.send_message = AsyncMock(side_effect=[PreSubmitError("token=private"), "deepseek answer"])

        with patch("wmadapter.service.DeepSeekChat", return_value=chat), self.assertLogs("wmadapter.service", level="INFO") as captured:
            result = await provider.complete("hello", conversation_id="deepseek-test")

        self.assertEqual(result, "deepseek answer")
        self.assertEqual(chat.send_message.await_count, 2)
        provider.browser.restart.assert_awaited_once()
        self.assertNotIn("private", "\n".join(captured.output))
        self.assertTrue(any("classification=pre_submit" in line for line in captured.output))

    async def test_qwen_retries_pre_submit_failure_but_not_uncertain_submission(self):
        provider = QwenService(self._config("qwen"))
        page = AsyncMock()
        page.url = "https://chat.qwen.ai/chat"
        provider._page_for_conversation = AsyncMock(return_value=page)
        provider._authenticate = AsyncMock()
        provider.browser.restart = AsyncMock()
        chat = Mock()
        chat.send_message = AsyncMock(side_effect=[PreSubmitError("before send"), "qwen answer"])

        with patch("wmadapter.service.QwenChat", return_value=chat):
            result = await provider.complete("hello", conversation_id="qwen-test")

        self.assertEqual(result, "qwen answer")
        self.assertEqual(chat.send_message.await_count, 2)
        provider.browser.restart.assert_awaited_once()

        uncertain = Mock()
        uncertain.send_message = AsyncMock(side_effect=UncertainSubmitError("accepted"))
        uncertain.recover_response = AsyncMock(return_value=None)
        provider.browser.restart.reset_mock()
        with patch("wmadapter.service.QwenChat", return_value=uncertain):
            with self.assertRaises(UncertainSubmitError):
                await provider.complete("side effect", conversation_id="qwen-test")
        uncertain.send_message.assert_awaited_once_with("side effect")
        provider.browser.restart.assert_not_awaited()

        provider.recovery_policy = RecoveryPolicy(
            enabled=True, max_attempts=2, backoff_base_ms=0, backoff_max_ms=0,
            deadline_ms=1000, allow_resend=True,
        )
        resend = Mock()
        resend.send_message = AsyncMock(side_effect=[UncertainSubmitError("accepted"), "safe text retry"])
        resend.recover_response = AsyncMock(return_value=None)
        with patch("wmadapter.service.QwenChat", return_value=resend), recovery_context(has_tools_or_side_effects=False):
            result = await provider.complete("plain text", conversation_id="qwen-test")
        self.assertEqual(result, "safe text retry")
        self.assertEqual(resend.send_message.await_count, 2)

    async def test_recovery_deadline_stops_pre_submit_retry(self):
        provider = DeepSeekService(self._config("deepseek"))
        provider.recovery_policy = RecoveryPolicy(
            enabled=True, max_attempts=3, backoff_base_ms=0, backoff_max_ms=0, deadline_ms=0
        )
        provider._page_for_conversation = AsyncMock(return_value=AsyncMock())
        provider._authenticate = AsyncMock()
        provider.browser.restart = AsyncMock()
        chat = Mock()
        chat.send_message = AsyncMock(side_effect=PreSubmitError("temporary"))
        with patch("wmadapter.service.DeepSeekChat", return_value=chat):
            with self.assertRaises(PreSubmitError):
                await provider.complete("hello")
        chat.send_message.assert_awaited_once()
        provider.browser.restart.assert_not_awaited()

    async def test_qwen_reconciles_late_response_without_a_second_send(self):
        from wmadapter.providers.qwen.chat import QwenChat

        chat = QwenChat(AsyncMock())
        chat.submit_state = SubmitState.SUBMITTED_UNCERTAIN
        chat._previous_counts = {}
        chat._latest_response_text = AsyncMock(return_value="late qwen answer")
        clock = Mock()
        clock.time.side_effect = [0, 0, 0, 0]
        with patch("wmadapter.providers.qwen.chat.asyncio.get_running_loop", return_value=clock), \
             patch("wmadapter.providers.qwen.chat.asyncio.sleep", new=AsyncMock()):
            result = await chat.recover_response(1000)

        self.assertEqual(result, "late qwen answer")
        self.assertEqual(chat.submit_state, SubmitState.COMPLETED)
        chat._latest_response_text.assert_awaited()


if __name__ == "__main__":
    unittest.main()
