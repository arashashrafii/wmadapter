# Live OpenAI Compatibility Test Plan

This is an opt-in suite for the authenticated MimicGate/WebChat runtime. It is
not part of the default Python suite or CI and must never be run accidentally.

## Safety gate

The runner requires both independent confirmations:

```bash
MIMICGATE_LIVE_COMPAT=1 \
  python -m tests.live_compatibility --confirm-live \
  --base-url http://127.0.0.1:11556/v1 \
  --model deepseek-chat --format markdown --output live-compatibility-report.md
```

The endpoint, model, and authenticated isolated profile must be explicitly
selected. Use `--group contract`, `--group tools`, or `--case T50` to limit the
run. JSON is the default report format; `--format markdown` writes a readable
matrix. Reports redact credentials, bearer values, API keys, tokens, and
passwords. Never put secrets or sensitive chat content in case data.

## Canonical cases

`tests/live_compatibility/cases.py` is the canonical T01–T50 definition. Every
case records goal, preconditions, method, expected result, actual result, and
status. The groups are contract/shape (T01–T10), normalization (T11–T18),
tools (T19–T30), conversation fidelity (T31–T38), streaming/errors (T39–T48),
and golden compatibility (T49–T50).

Assertions compare protocol semantics only: HTTP status, envelope, types,
roles, finish reasons, tool structure, JSON arguments, SSE framing, safe error
codes, and SDK/agent deserialization evidence. They do not compare generated
answer text with an OpenAI reference model.

## Status rules

- `PASS`: semantic assertions succeeded.
- `FAIL`: the endpoint responded but a required semantic assertion failed.
- `BLOCKED`: a prerequisite was unavailable, such as authentication, endpoint,
  SDK, OpenClaw configuration, or provider access. The report must name the
  prerequisite without exposing secrets.

The live suite has no bypass for CAPTCHA, login, browser isolation, or provider
readiness. Resolve those prerequisites manually, then rerun the selected cases.
Unit tests in `tests/test_live_compatibility.py` validate definitions, guards,
assertions, and report rendering without network access.
