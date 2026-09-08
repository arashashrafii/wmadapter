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


class LiveCompatibilityUnitTests(unittest.TestCase):
    def test_definitions_are_canonical_and_complete(self):
        self.assertEqual(len(CASES), 50)
        self.assertEqual([case.case_id for case in CASES], [f"T{i:02d}" for i in range(1, 51)])
        self.assertEqual(set(case.group for case in CASES), set(GROUPS))
        for case in CASES:
            self.assertTrue(all(getattr(case, field) for field in ("goal", "preconditions", "method", "expected")))

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
        self.assertNotIn("secret-value", json.dumps(redact({"api_key": "secret-value", "message": "Bearer abc"})))

    def test_reports_support_json_and_markdown(self):
        report = new_report([{"case_id": "T01", "group": "contract", "status": "PASS", "actual": "HTTP 200"}])
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "report.json"
            md_path = Path(directory) / "report.md"
            write_report(report, json_path, "json")
            write_report(report, md_path, "markdown")
            self.assertEqual(json.loads(json_path.read_text())["results"][0]["case_id"], "T01")
            self.assertIn("| T01 | contract | PASS |", md_path.read_text())
