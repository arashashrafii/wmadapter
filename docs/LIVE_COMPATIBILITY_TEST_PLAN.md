# Live OpenAI Compatibility Test Plan

The canonical suite is `tests/live_compatibility`. It contains 62 executable
case specs, T01–T62, with distinct payload builders and semantic expectations.
Evidence schema version 2 records each case's `execution` mode (`client` or
`gateway`) and `applicability` (`applicable` or `not_applicable`).
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

Live cases are intentionally executed sequentially: one request is sent and
verified before the next case starts. Do not parallelize cases or run the full
suite against a personal provider account; use the fixture transport for broad
coverage and a dedicated provider account for bounded live smoke tests.

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
`FixtureTransport` exercises all 62 cases offline; T54 is recorded as
`BLOCKED`/`not_applicable` because `image_input` is disabled. T51–T56 are
OpenCode cases and T57–T62 are OpenClaw cases. Client-success cases cover
baseline text, SSE ordering, and one fixture tool round trip. Negative media
and readiness cases run as direct gateway checks: T55 must observe HTTP 400
with `unsupported_feature`, and T56 must observe HTTP 503 with
`provider_not_ready`. A mismatched status or code is `FAIL`, never an accepted
alternative.
Client harnesses are opt-in and require explicit JSON argv/config paths through
`WMADAPTER_OPENCODE_COMMAND`, `WMADAPTER_OPENCODE_CONFIG`,
`WMADAPTER_OPENCLAW_COMMAND`, and `WMADAPTER_OPENCLAW_CONFIG`; no CLI flags are
guessed and no shell is used. OpenAI and OpenClaw adapters
report `BLOCKED` when the official client or configured executable is absent.
Only the exact documented gateway status and error code is a passing negative
case. Optional client error propagation may pass from captured error evidence
without a `step_finish` event, but it must inspect the captured HTTP status.
Missing OpenClaw command/config is a structured preflight `BLOCKED` result.
No image case is evidence of vision while `image_input` is disabled.

Run offline validation with:

```bash
PYTHONPATH=tests:src python -m unittest test_live_compatibility
```
