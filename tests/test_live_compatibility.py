import json
import os
import tempfile
import unittest
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from live_compatibility.assertions import assert_completion_semantics, assert_safe_error, assert_sse_semantics
from live_compatibility.cases import CASES, GROUPS
from live_compatibility.report import new_report, redact, write_report
from live_compatibility.runner import require_live_confirmation
from live_compatibility.runner import LiveContext, run_suite
from live_compatibility.runner import resolve_url
from live_compatibility.adapters import run_openai_sdk, run_openclaw, run_client_case
from live_compatibility.transport import FixtureTransport, SSEParser, TransportResponse


class LiveCompatibilityUnitTests(unittest.TestCase):
    def test_definitions_are_canonical_and_complete(self):
        self.assertEqual(len(CASES), 62)
        self.assertEqual([case.case_id for case in CASES], [f"T{i:02d}" for i in range(1, 63)])
        self.assertEqual(set(case.group for case in CASES), set(GROUPS))
        for case in CASES:
            self.assertTrue(all(getattr(case, field) for field in ("goal", "preconditions", "method", "expected")))
            self.assertTrue(callable(case.payload_builder))
        self.assertEqual(len({case.payload_builder.__name__ for case in CASES}), 62)
        self.assertGreaterEqual(len({json.dumps(case.payload("deepseek-chat"), sort_keys=True) for case in CASES}), 57)

    def test_live_guard_requires_environment_and_confirmation(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "WMADAPTER_LIVE_COMPAT"):
                require_live_confirmation(True)
        with patch.dict(os.environ, {"WMADAPTER_LIVE_COMPAT": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "confirm-live"):
                require_live_confirmation(False)
            require_live_confirmation(True)

    def test_url_resolution_keeps_v1_for_completion_and_uses_root_for_ready(self):
        self.assertEqual(resolve_url("http://127.0.0.1:11556/v1", "completion"), "http://127.0.0.1:11556/v1/chat/completions")
        self.assertEqual(resolve_url("http://127.0.0.1:11556/v1", "ready"), "http://127.0.0.1:11556/ready")

    def test_openai_adapter_uses_typed_sdk_response(self):
        message = SimpleNamespace(role="assistant", content="ok")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
        fake_module = SimpleNamespace(OpenAI=lambda **kwargs: client)
        with patch.dict(sys.modules, {"openai": fake_module}), patch("importlib.util.find_spec", return_value=object()), patch.dict(os.environ, {"WMADAPTER_LIVE_API_KEY": "local-test"}):
            status, detail = run_openai_sdk(LiveContext("http://localhost:11556/v1", "deepseek-chat"), {"model": "deepseek-chat", "messages": []})
        self.assertEqual(status, "PASS"); self.assertIn("typed", detail)

    def test_openai_adapter_classifies_provider_unavailable_as_blocked(self):
        class ProviderUnavailable(Exception):
            status_code = 503
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(ProviderUnavailable()))) )
        fake_module = SimpleNamespace(OpenAI=lambda **kwargs: client)
        with patch.dict(sys.modules, {"openai": fake_module}), patch("importlib.util.find_spec", return_value=object()), patch.dict(os.environ, {"WMADAPTER_LIVE_API_KEY": "local-test"}):
            status, detail = run_openai_sdk(LiveContext("http://localhost:11556/v1", "deepseek-chat"), {"model": "deepseek-chat", "messages": []})
        self.assertEqual(status, "BLOCKED"); self.assertIn("503", detail)

    def test_openclaw_adapter_is_blocked_without_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            status, detail = run_openclaw(LiveContext("http://localhost:11556/v1", "deepseek-chat"), "hello")
        self.assertEqual(status, "BLOCKED"); self.assertIn("COMMAND", detail)

    def test_semantic_assertions_and_safe_redaction(self):
        assert_completion_semantics({"id": "chatcmpl-x", "object": "chat.completion", "created": 1, "model": "deepseek-chat", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}, "deepseek-chat")
        assert_sse_semantics('data: {"object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                            'data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                            'data: [DONE]\n\n')
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
        with patch.dict(os.environ, {"WMADAPTER_LIVE_COMPAT": "1"}):
            report = run_suite(context=LiveContext("fixture://offline", "deepseek-chat"), confirm_live=True, transport=FixtureTransport())
        self.assertEqual(len(report["results"]), 62)
        self.assertTrue(all(item["status"] == "PASS" for item in report["results"]))

    def test_malformed_messages_case_preserves_empty_message_list(self):
        case = next(case for case in CASES if case.case_id == "T45")
        self.assertEqual(case.payload("deepseek-chat")["messages"], [])

    def test_readiness_preflight_blocks_without_case_requests(self):
        class Down:
            def ready(self): return TransportResponse(503, "application/json", "{}")
            def request(self, case, payload): self.called = True; raise AssertionError("must not request")
        with patch.dict(os.environ, {"WMADAPTER_LIVE_COMPAT": "1"}):
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

    def test_client_cases_are_separate_and_cover_required_behaviors(self):
        for client in ("opencode", "openclaw"):
            cases = [case for case in CASES if case.client == client]
            self.assertEqual([case.kind for case in cases], ["completion", "sse", "tool_roundtrip", "image", "unsupported_media", "provider_not_ready"])

    def test_sse_assertion_classifies_progressive_and_buffered_streams(self):
        from live_compatibility.assertions import assert_sse_semantics
        progressive = ('data: {"object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                       'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"a"},"finish_reason":null}]}\n\n'
                       'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"b"},"finish_reason":null}]}\n\n'
                       'data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        buffered = progressive.replace('"content":"a"},"finish_reason":null}]}\n\ndata: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"b"},"finish_reason":null}', '"content":"ab"},"finish_reason":null}]}\n\ndata: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":null}')
        self.assertEqual(assert_sse_semantics(progressive), "progressive")
        self.assertEqual(assert_sse_semantics(buffered), "buffered")

    def test_client_case_harness_uses_explicit_argv_and_validates_evidence(self):
        case = next(case for case in CASES if case.client == "opencode" and case.kind == "completion")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "opencode.json"
            config.write_text("{}")
            completed = SimpleNamespace(returncode=0, stdout=json.dumps({"status": "ok", "provider": "wmadapter", "model": "deepseek-chat"}), stderr="")
            with patch.dict(os.environ, {"WMADAPTER_OPENCODE_COMMAND": json.dumps(["opencode", "compat-harness"]), "WMADAPTER_OPENCODE_CONFIG": str(config)}), patch("subprocess.run", return_value=completed) as run:
                status, detail = run_client_case(LiveContext("http://localhost:11556/v1", "deepseek-chat"), case, case.payload("deepseek-chat"))
            self.assertEqual(status, "PASS")
            self.assertIn("opencode", detail)
            self.assertEqual(run.call_args.kwargs["shell"], False)

    def test_client_case_harness_requires_explicit_configuration(self):
        case = next(case for case in CASES if case.client == "openclaw" and case.kind == "image")
        with patch.dict(os.environ, {}, clear=True):
            status, detail = run_client_case(LiveContext("http://localhost:11556/v1", "deepseek-chat"), case, case.payload("deepseek-chat"))
        self.assertEqual(status, "BLOCKED")
        self.assertIn("WMADAPTER_OPENCLAW_COMMAND", detail)
