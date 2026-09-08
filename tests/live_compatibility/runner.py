from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .assertions import assert_completion_semantics, assert_safe_error, assert_sse_semantics
from .cases import CASES, GROUPS
from .report import new_report, write_report


def require_live_confirmation(confirm_live: bool) -> None:
    if os.environ.get("MIMICGATE_LIVE_COMPAT") != "1":
        raise RuntimeError("BLOCKED: set MIMICGATE_LIVE_COMPAT=1 to enable live compatibility tests")
    if not confirm_live:
        raise RuntimeError("BLOCKED: pass --confirm-live to enable live compatibility tests")


def _post(url: str, payload: dict, timeout: float):
    request = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get_content_type(), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get_content_type(), error.read().decode()


def run_suite(*, base_url: str, model: str, confirm_live: bool, groups=None, case_ids=None, timeout: float = 30.0) -> dict:
    require_live_confirmation(confirm_live)
    selected = [case for case in CASES if (not groups or case.group in groups) and (not case_ids or case.case_id in case_ids)]
    report = new_report([])
    for case in selected:
        started = time.monotonic()
        result = {"case_id": case.case_id, "group": case.group, "title": case.title, "goal": case.goal, "preconditions": case.preconditions, "method": case.method, "expected": case.expected, "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            status, content_type, body = _post(base_url, {**case.request, "model": model}, timeout)
            parsed = json.loads(body)
            if status >= 400:
                assert_safe_error(parsed, status)
            elif case.request.get("stream"):
                assert_sse_semantics(body)
            else:
                assert_completion_semantics(parsed, model)
            result.update(status="PASS", actual=f"HTTP {status}; {content_type}")
        except (AssertionError, ValueError, urllib.error.URLError) as error:
            result.update(status="FAIL", actual=str(error)[:300])
        except Exception as error:
            result.update(status="BLOCKED", actual=f"{type(error).__name__}: {error}"[:300])
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        report["results"].append(result)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in MimicGate live compatibility suite")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--base-url", default=os.environ.get("MIMICGATE_LIVE_URL", "http://127.0.0.1:11556/v1"))
    parser.add_argument("--model", default=os.environ.get("MIMICGATE_LIVE_MODEL", "deepseek-chat"))
    parser.add_argument("--group", action="append", choices=sorted(GROUPS))
    parser.add_argument("--case", dest="case_ids", action="append")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", default="live-compatibility-report.json")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_suite(base_url=args.base_url, model=args.model, confirm_live=args.confirm_live, groups=args.group, case_ids=args.case_ids, timeout=args.timeout)
    except RuntimeError as error:
        parser.error(str(error))
    write_report(report, __import__("pathlib").Path(args.output), args.format)
    return 0 if all(item["status"] == "PASS" for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
