# Web Model Adapter E2E Test Plan

Canonical baseline: GitHub Issue #41. Run against a clean checkout of the
current `main` commit. Every step must record its goal, preconditions, method,
expected result, and actual result as PASS, FAIL, or BLOCKED.

## Baseline sequence

1. Clean-environment installation
2. Python environment creation
3. Dependency installation
4. System Google Chrome installation/availability
5. Service execution
6. Provider login opens
7. Isolated profile verification
8. Single-page/browser lifecycle verification
9. CAPTCHA without refresh/relaunch
10. Login success detection
11. Headed-to-headless handoff
12. `/health` and `/ready`
13. OpenAI-compatible API smoke test

## Execution contract

For each step, record:

- **Goal** — the behavior being verified.
- **Preconditions** — clean checkout, runtime, browser, display, profile, or
  authenticated session required by the step.
- **Method** — exact command, endpoint, observation, or fixture used.
- **Expected result** — the acceptance condition.
- **Actual result** — PASS, FAIL, or BLOCKED with evidence and timestamp.

The live provider steps are user-driven. CAPTCHA must remain in the same page
and browser; the test must not automate credential entry, refresh the page, or
relaunch the browser. Use a disposable profile for destructive or repeated
live checks. Do not claim a clean-install or handoff PASS from an existing
service observation alone.

## Current baseline record

The current execution is recorded in Issue #41. At the time of the 2026-09-08
baseline, Python unit/contract and shell checks passed, while clean test
dependency installation, browser binary availability, service availability in
the current checkout, page-capacity/lifecycle behavior, CAPTCHA no-relaunch,
and the live API smoke remained FAIL or BLOCKED. The generic Playwright fixture
is not a Web Model Adapter E2E suite and must not be treated as provider verification.

## Required rerun gates

After any blocker fix, rerun all dependent steps from step 5 through step 13.
Do not close #41 until all thirteen steps have a current result and evidence,
and `/ready` plus the API smoke pass after the handoff without page-capacity or
profile-ownership errors.

## Issue #52 compatibility acceptance

Run the deterministic contract gate from the repository root:

```bash
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Run the focused client gate for OpenCode/OpenClaw-compatible behavior:

```bash
PYTHONPATH=src:tests .venv/bin/python -m unittest \
  tests.test_openai_compatibility tests.test_http_contract \
  tests.test_responses_endpoint tests.test_opencode_channel
```

These gates cover Chat Completions, legacy Completions, text Responses,
buffered SSE and usage chunks, serial function-tool round trips and tool-choice
validation, model metadata, OpenCode routing, and generic OpenClaw behavior.
They also cover explicit validation/`501` behavior for embeddings, images,
audio, Realtime, files/PDFs, batches, unsupported media, sampling controls,
custom tools, and parallel execution.

For an authenticated, user-confirmed provider check, run only after `/ready`
is healthy:

```bash
WMADAPTER_LIVE_COMPAT=1 \
PYTHONPATH=src:tests .venv/bin/python -m tests.live_compatibility \
  --confirm-live --group golden --format markdown --output live-compatibility-report.md
```

The live suite may be `BLOCKED` for readiness, login, capacity, or client
prerequisites. It must not automate credentials, CAPTCHA, or browser relaunch.
Reports may contain statuses and safe evidence only: never persist credentials,
browser session state, prompts, or provider transcripts. Passing text/tool
cases does not verify vision, multimodal, audio, video, file/PDF, embeddings,
or batch capability.

### Issue #52 endpoint checklist

| Surface | Acceptance condition |
| --- | --- |
| `/health`, `/ready`, `/props`, `/v1/models` | Health/readiness plus truthful provider, capability, and known-limit metadata |
| `/v1/chat/completions` | Text, tool history, serial function tools, supported choices, buffered SSE, observed-or-null usage |
| `/v1/completions` | Documented legacy text subset; unsupported fields rejected |
| `/v1/responses` | Non-streaming text response; tools/media/sampling unsupported explicitly |
| OpenCode route and OpenClaw standard route | Existing routing and client-side tool-result loop preserved |
| Embeddings/images/audio/Realtime/files/batches | Validation followed by explicit `501`; no fake output, storage, or jobs |
| Image/video/audio/file/PDF input | Explicit rejection; no unverified vision or multimodal claim |
