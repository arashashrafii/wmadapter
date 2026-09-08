import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live_compatibility.assertions import assert_completion_semantics, assert_safe_error, assert_sse_semantics
from live_compatibility.cases import CASES, GROUPS
from live_compatibility.report import new_report, redact, write_report
from live_compatibility.runner import require_live_confirmation
from live_compatibility.runner import LiveContext, run_suite
from live_compatibility.transport import FixtureTransport, SSEParser, TransportResponse


class LiveCompatibilityUnitTests(unittest.TestCase):
    def test_definitions_are_canonical_and_complete(self):
        self.assertEqual(len(CASES), 50)
        self.assertEqual([case.case_id for case in CASES], [f"T{i:02d}" for i in range(1, 51)])
        self.assertEqual(set(case.group for case in CASES), set(GROUPS))
        for case in CASES:
            self.assertTrue(all(getattr(case, field) for field in ("goal", "preconditions", "method", "expected")))
            self.assertTrue(callable(case.payload_builder))
        self.assertEqual(len({case.payload_builder.__name__ for case in CASES}), 50)
        self.assertEqual(len({json.dumps(case.payload("deepseek-chat"), sort_keys=True) for case in CASES}), 50)

    def test_live_guard_requires_environment_and_confirmation(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MIMICGATE_LIVE_COMPAT"):
                require_live_confirmation(True)
        with patch.dict(os.environ, {"MIMICGATE_LIVE_COMPAT": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "confirm-live"):
                require_live_confirmation(False)
            require_live_confirmation(True)

    def test_semantic_assertions_and_safe_redaction(self):
        assert_completion_semantics({"id": "chatcmpl-x", "object": "chat.completion", "created": 1, "model": "deepseek-chat", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}, "deepseek-chat")
        assert_sse_semantics('data: {"object":"chat.completion.chunk"}\n\ndata: [DONE]\n\n')
        assert_safe_error({"error": {"type": "provider_error", "code": "provider_not_ready", "message": "retry"}}, 503)
        safe = redact({"api_key": "secret-value", "prompt": "private", "profile_path": "/private/profile", "message": "Bearer abc"})
        self.assertNotIn("secret-value", json.dumps(safe)); self.assertNotIn("private/profile", json.dumps(safe)); self.assertEqual(safe["prompt"], "[REDACTED]")

    def test_sse_parser_handles_split_lines_and_all_line_endings(self):
        parser = SSEParser(); events = []
        for chunk in ["data: {\"x\":", "1}\r", "\ndata: [DO", "NE]\r\n", "\r\n"]:
            events.extend(parser.feed(chunk))
        events.extend(parser.finish())
        self.assertEqual(events, ['{"x":1}', '[DONE]'])

    def test_fixture_transport_executes_all_cases_offline(self):
        with patch.dict(os.environ, {"MIMICGATE_LIVE_COMPAT": "1"}):
            report = run_suite(context=LiveContext("fixture://offline", "deepseek-chat"), confirm_live=True, transport=FixtureTransport())
        self.assertEqual(len(report["results"]), 50)
        self.assertTrue(all(item["status"] == "PASS" for item in report["results"]))

    def test_readiness_preflight_blocks_without_case_requests(self):
        class Down:
            def ready(self): return TransportResponse(503, "application/json", "{}")
            def request(self, case, payload): self.called = True; raise AssertionError("must not request")
        with patch.dict(os.environ, {"MIMICGATE_LIVE_COMPAT": "1"}):
            report = run_suite(context=LiveContext("fixture://offline", "deepseek-chat"), confirm_live=True, transport=Down())
        self.assertTrue(all(item["status"] == "BLOCKED" for item in report["results"]))

    def test_reports_support_json_and_markdown(self):
        report = new_report([{"case_id": "T01", "group": "contract", "status": "PASS", "actual": "HTTP 200"}])
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "report.json"
            md_path = Path(directory) / "report.md"
            write_report(report, json_path, "json")
            write_report(report, md_path, "markdown")
            self.assertEqual(json.loads(json_path.read_text())["results"][0]["case_id"], "T01")
            self.assertIn("| T01 | contract | PASS |", md_path.read_text())
