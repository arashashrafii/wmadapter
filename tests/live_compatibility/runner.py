from __future__ import annotations

import argparse
import json
import os
import time
import json as _json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .assertions import assert_completion_semantics, assert_gateway_error, assert_safe_error, assert_sse_semantics, assert_tool_roundtrip
from .adapters import run_openai_sdk, run_openclaw, run_client_case
from .cases import CASES, GROUPS
from .report import new_report, write_report
from .transport import FixtureTransport, TransportResponse


@dataclass(frozen=True)
class LiveContext:
    base_url: str
    model: str
    profile: str | None = None
    timeout: float = 30.0

    def public(self):
        return {"base_url": self.base_url, "model": self.model, "timeout": self.timeout}


def resolve_url(base_url: str, endpoint: str) -> str:
    parts = urlsplit(base_url)
    base_path = parts.path.rstrip("/")
    path = base_path + "/chat/completions" if endpoint == "completion" else (base_path.rsplit("/", 1)[0] or "") + "/ready"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class LiveTransport:
    def __init__(self, context): self.context = context
    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(resolve_url(self.context.base_url, "completion" if path == "/chat/completions" else "ready"), data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.context.timeout) as response:
                return TransportResponse(response.status, response.headers.get_content_type(), response.read().decode())
        except urllib.error.HTTPError as error:
            return TransportResponse(error.code, error.headers.get_content_type(), error.read().decode())
    def ready(self): return self._request("/ready")
    def request(self, case, payload): return self._request("/chat/completions", payload)


def require_live_confirmation(confirm_live):
    if os.environ.get("WMADAPTER_LIVE_COMPAT") != "1": raise RuntimeError("BLOCKED: set WMADAPTER_LIVE_COMPAT=1")
    if not confirm_live: raise RuntimeError("BLOCKED: pass --confirm-live")


def run_suite(*, context, confirm_live, groups=None, case_ids=None, transport=None):
    require_live_confirmation(confirm_live)
    transport = transport or LiveTransport(context)
    report = new_report([]); report["metadata"] = context.public()
    preflight_error = None
    try:
        preflight = transport.ready() if hasattr(transport, "ready") else None
    except (ConnectionRefusedError, TimeoutError, urllib.error.URLError, OSError) as error:
        # Readiness is a prerequisite. A network failure blocks the suite and
        # must still produce the structured report; clients are never invoked.
        preflight = TransportResponse(503, "application/json", "{}")
        preflight_error = error
    is_live = not isinstance(transport, FixtureTransport)
    selected = [case for case in CASES if (not groups or case.group in groups) and (not case_ids or case.case_id in case_ids)]
    if preflight_error is not None:
        report["preflight"] = {"status": "BLOCKED", "actual": f"live readiness unavailable: {type(preflight_error).__name__}"}
    elif preflight is not None and preflight.status != 200:
        report["preflight"] = {"status": "BLOCKED", "actual": f"HTTP {preflight.status}; provider readiness unavailable"}
    openclaw_config_blocked = False
    if (is_live and not preflight_error and (preflight is None or preflight.status == 200)
            and any(case.client == "openclaw" and case.execution == "client" and case.applicability == "applicable" for case in selected)):
        if not os.environ.get("WMADAPTER_OPENCLAW_COMMAND") or not os.environ.get("WMADAPTER_OPENCLAW_CONFIG"):
            report["preflight"] = {"status": "BLOCKED", "actual": "WMADAPTER_OPENCLAW_COMMAND and WMADAPTER_OPENCLAW_CONFIG are required"}
            openclaw_config_blocked = True
    for case in selected:
        result = {"case_id": case.case_id, "group": case.group, "title": case.title, "goal": case.goal, "preconditions": case.preconditions, "method": case.method, "expected": case.expected, "execution": case.execution, "applicability": case.applicability, "started_at": datetime.now(timezone.utc).isoformat()}
        if case.applicability == "not_applicable":
            result.update(status="BLOCKED", actual="not applicable while image_input capability is disabled")
            report["results"].append(result); continue
        if openclaw_config_blocked and case.client == "openclaw" and case.execution == "client":
            result.update(status="BLOCKED", actual="OpenClaw preflight configuration is unavailable")
            report["results"].append(result); continue
        if (preflight is not None and preflight.status != 200
                and (case.execution != "gateway" or not isinstance(transport, LiveTransport))):
            result.update(status="BLOCKED", actual=f"HTTP {preflight.status}: provider not ready"); report["results"].append(result); continue
        if case.execution == "gateway":
            try:
                response = transport.request(case, case.payload(context.model))
                expected_code = "provider_not_ready" if case.kind == "provider_not_ready" else "unsupported_feature"
                if response.status != case.expected_status:
                    result.update(status="FAIL", actual=f"expected HTTP {case.expected_status}, observed HTTP {response.status}")
                else:
                    assert_gateway_error(response.body, response.status, expected_code)
                    result.update(status="PASS", actual=f"HTTP {response.status}; code={expected_code}")
            except (AssertionError, ValueError) as error:
                result.update(status="FAIL", actual=str(error)[:300])
            except (TimeoutError, urllib.error.URLError, OSError) as error:
                result.update(status="BLOCKED", actual=f"gateway request unavailable: {type(error).__name__}")
            report["results"].append(result); continue
        if is_live and case.case_id == "T49":
            status, detail = run_openai_sdk(context, case.payload(context.model)); result.update(status=status, actual=detail); report["results"].append(result); continue
        if is_live and case.case_id == "T50":
            status, detail = run_openclaw(context, "Reply with a short compatibility acknowledgment.")
            if status == "PASS" and os.environ.get("WMADAPTER_OPENCLAW_TOOL_ARGS"):
                try: _json.loads(os.environ["WMADAPTER_OPENCLAW_TOOL_ARGS"])
                except _json.JSONDecodeError: status, detail = "BLOCKED", "WMADAPTER_OPENCLAW_TOOL_ARGS must be a JSON argument list"
                else: status, detail = run_openclaw(context, "Run the configured compatibility tool-loop and report its result.", include_tools=True)
            result.update(status=status, actual=detail); report["results"].append(result); continue
        if is_live and case.client in {"opencode", "openclaw"}:
            status, detail = run_client_case(context, case, case.payload(context.model))
            result.update(status=status, actual=detail); report["results"].append(result); continue
        try:
            response = transport.request(case, case.payload(context.model))
            if response.status != case.expected_status:
                if response.status in {401, 408, 429, 502, 503, 504}: result.update(status="BLOCKED", actual=f"HTTP {response.status}: live prerequisite unavailable")
                else: result.update(status="FAIL", actual=f"unexpected HTTP {response.status}")
            elif response.status >= 400:
                assert_safe_error(json.loads(response.body), response.status); result.update(status="PASS", actual=f"HTTP {response.status}; exact expected fixture error")
            elif case.stream:
                classification = assert_sse_semantics(response.body)
                result.update(status="PASS", actual=f"SSE semantics valid; classification={classification}")
            elif case.kind == "tool_roundtrip":
                call_id = assert_tool_roundtrip(json.loads(response.body), context.model)
                continuation = dict(case.payload(context.model))
                continuation["messages"] = continuation["messages"] + [
                    {"role": "assistant", "content": None, "tool_calls": json.loads(response.body)["choices"][0]["message"]["tool_calls"]},
                    {"role": "tool", "tool_call_id": call_id, "content": "fixture-nonce"},
                ]
                follow_up = transport.request(case, continuation)
                assert_completion_semantics(json.loads(follow_up.body), context.model)
                result.update(status="PASS", actual="tool call and tool result continuation semantics valid")
            else:
                assert_completion_semantics(json.loads(response.body), context.model); result.update(status="PASS", actual="completion semantics valid")
        except (AssertionError, ValueError) as error: result.update(status="FAIL", actual=str(error)[:300])
        except (TimeoutError, urllib.error.URLError) as error: result.update(status="BLOCKED", actual=f"live prerequisite unavailable: {type(error).__name__}")
        report["results"].append(result)
    return report


def build_parser():
    parser = argparse.ArgumentParser(description="Opt-in Web Model Adapter live compatibility suite")
    parser.add_argument("--confirm-live", action="store_true"); parser.add_argument("--base-url", default=os.environ.get("WMADAPTER_LIVE_URL", "http://127.0.0.1:11556/v1")); parser.add_argument("--model", default=os.environ.get("WMADAPTER_LIVE_MODEL", "deepseek-chat")); parser.add_argument("--profile", default=None); parser.add_argument("--group", action="append", choices=sorted(GROUPS)); parser.add_argument("--case", dest="case_ids", action="append"); parser.add_argument("--format", choices=("json", "markdown"), default="json"); parser.add_argument("--output", default="live-compatibility-report.json"); parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try: report = run_suite(context=LiveContext(args.base_url, args.model, args.profile, args.timeout), confirm_live=args.confirm_live, groups=args.group, case_ids=args.case_ids)
    except RuntimeError as error: build_parser().error(str(error))
    write_report(report, Path(args.output), args.format)
    return 0 if all(item["status"] == "PASS" for item in report["results"]) else 1


if __name__ == "__main__": raise SystemExit(main())
