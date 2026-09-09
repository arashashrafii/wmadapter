# Live OpenAI Compatibility Test Plan

The canonical suite is `tests/live_compatibility`. It contains 50 executable
case specs, T01–T50, with distinct payload builders and semantic expectations.
It is opt-in and is never imported by the default test suite or CI.

## Safety and prerequisites

Live execution requires both a deliberate environment gate and a CLI flag:

```bash
WMADAPTER_LIVE_COMPAT=1 python -m tests.live_compatibility \
  --confirm-live --base-url http://127.0.0.1:11556/v1 \
  --model deepseek-chat --format markdown --output live-report.md
```

The runner performs a `/ready` preflight before any case. A live 503, outage,
timeout, authentication failure, or unavailable SDK/OpenClaw adapter is
`BLOCKED`, not `PASS`. No CAPTCHA bypass, profile deletion, or destructive
cleanup is performed. Select `--group contract`, `--group tools`, or repeated
`--case T50` values for a bounded run.

With a base URL ending in `/v1`, preflight resolves to `/ready` and completion
resolves to `/v1/chat/completions`. T49 requires the official `openai` Python
package and `WMADAPTER_LIVE_API_KEY`; it constructs `OpenAI(...).chat.completions`
and validates the typed response. T50 requires `WMADAPTER_OPENCLAW_COMMAND` as a
JSON argv list and `WMADAPTER_OPENCLAW_CONFIG`; it runs exactly those arguments
via `subprocess` (no shell and no guessed flags), validates exit status and
configured endpoint evidence, and runs an optional configured tool-loop using
`WMADAPTER_OPENCLAW_TOOL_ARGS`.

## Evidence and assertions

Each result records goal, preconditions, method, expected, actual, timestamp,
duration, and status. JSON and Markdown reports are supported. Redaction removes
prompts, responses, credentials, authorization values, tokens, secrets, and
profile/path fields from evidence. Reports compare HTTP semantics, schema,
roles, finish reasons, tool IDs/choices/results/JSON arguments, conversation
identity/isolation, SSE reconstruction, and SDK/agent deserialization; they do
not compare nondeterministic model text with a reference model.

`SSEParser` is incremental and handles LF, CRLF, CR, and chunk boundaries.
`FixtureTransport` exercises all 50 cases offline. OpenAI and OpenClaw adapters
report `BLOCKED` when the official client or configured executable is absent.
Only an exact expected fixture error is a passing negative case; an unexpected
live provider error is a failure or blocked prerequisite according to the
status rules above.

Run offline validation with:

```bash
PYTHONPATH=tests:src python -m unittest test_live_compatibility
```
