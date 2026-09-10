import unittest

from wmadapter.main import _provider_http_error
from wmadapter.providers.contract import ProviderResult
from wmadapter.providers.errors import ProviderRateLimitError
from wmadapter.providers.submit import PreSubmitError, UncertainSubmitError


class ErrorSemanticsTests(unittest.TestCase):
    def test_rate_limit_has_retry_after_only_when_known(self):
        known = _provider_http_error(ProviderRateLimitError(retry_after=12))
        unknown = _provider_http_error(ProviderRateLimitError())

        self.assertEqual((known.status_code, known.detail), (429, "provider_rate_limited"))
        self.assertEqual(known.headers, {"Retry-After": "12"})
        self.assertEqual((unknown.status_code, unknown.detail), (429, "provider_rate_limited"))
        self.assertIsNone(unknown.headers)

    def test_timeout_uncertain_and_pre_submit_have_distinct_safe_categories(self):
        timeout = _provider_http_error(TimeoutError())
        uncertain = _provider_http_error(UncertainSubmitError())
        unavailable = _provider_http_error(PreSubmitError("browser unavailable"))

        self.assertEqual((timeout.status_code, timeout.detail), (504, "provider_timeout"))
        self.assertEqual((uncertain.status_code, uncertain.detail), (502, "provider_submission_uncertain"))
        self.assertEqual((unavailable.status_code, unavailable.detail), (503, "provider_unavailable"))

    def test_finish_reason_matches_tool_payload(self):
        with self.assertRaisesRegex(ValueError, "requires tool_calls"):
            ProviderResult(content=None, finish_reason="tool_calls")
        with self.assertRaisesRegex(ValueError, "finish_reason=tool_calls"):
            ProviderResult(content="answer", tool_calls=[{"id": "call", "type": "function"}])
        result = ProviderResult(
            content=None,
            tool_calls=[{"id": "call", "type": "function"}],
            finish_reason="tool_calls",
        )
        self.assertEqual(result.finish_reason, "tool_calls")


if __name__ == "__main__":
    unittest.main()
