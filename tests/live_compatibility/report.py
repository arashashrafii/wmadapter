from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

SECRET = re.compile(r"(?i)(bearer\s+|api[_-]?key\s*[:=]\s*|password\s*[:=]\s*)[^\s,;]+")


def redact(value):
    if isinstance(value, str):
        return SECRET.sub(r"\1[REDACTED]", value)
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.search(r"(?i)(key|token|password|secret|authorization)", str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def new_report(cases) -> dict:
    return {"schema_version": 1, "suite": "mimicgate-live-compatibility", "started_at": datetime.now(timezone.utc).isoformat(), "results": [redact(case) for case in cases]}


def write_report(report: dict, path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = redact(report)
    if fmt == "json":
        path.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n")
        return
    lines = ["# MimicGate Live Compatibility Report", "", f"Started: {safe['started_at']}", "", "| Case | Group | Status | Actual |", "|---|---|---|---|"]
    for result in safe["results"]:
        actual = str(result.get("actual", "")).replace("|", "\\|")
        lines.append(f"| {result['case_id']} | {result['group']} | {result['status']} | {actual} |")
    path.write_text("\n".join(lines) + "\n")
