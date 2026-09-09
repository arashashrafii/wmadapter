# OpenAI Compatibility Evidence

Issue #43 records compatibility in three layers. The suite is deterministic by
default and does not contact provider or OpenAI live services.

## L1 — HTTP/tools/SSE contract

Run the local wire-contract checks with:

```bash
PYTHONPATH=src:tests .venv/bin/python -m unittest tests.test_openai_compatibility tests.test_http_contract
```

These tests use a deterministic in-process provider fixture and cover ordinary
chat, tool-call/tool-result round trips, and buffered OpenAI-shaped SSE.

## L2 — Official SDK black box (optional)

Point the official OpenAI Python SDK at a running local fixture endpoint and
opt in explicitly:

```bash
WMADAPTER_OPENAI_COMPAT_L2=1 \
WMADAPTER_TEST_URL=http://127.0.0.1:18761/v1 \
PYTHONPATH=src .venv/bin/python -m unittest tests.test_openai_compatibility.OpenAICompatibilityL2Tests
```

Without the opt-in endpoint, the test is reported as `BLOCKED`/skipped. If the
official SDK is not installed, the test is reported as `SKIPPED`; no dependency
is added to the runtime package for this optional check.

## L3 — Fixture evidence

`tests/fixtures/openai_compatibility.json` is the tracked evidence record for
the deterministic text, tool-call, tool-result, and SSE cases. It is intentionally
local and synthetic: provider authentication, live OpenAI traffic, signatures,
and production behavior are not part of this issue.
