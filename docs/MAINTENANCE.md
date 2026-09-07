# Maintenance Policy

## Browser login interruption diagnostics

Inspect provider `lifecycle_events` when a managed login ends unexpectedly.
The final event identifies the boundary through `reason`: `user_close`,
`page_crash`, `context_close`, `playwright_disconnect`,
`display_session_failure`, or managed cleanup. Events include the login attempt
ID, browser generation, page count, authentication state, Chromium PID, and an
exit status when Playwright exposes the process.

`playwright_disconnect` means transport evidence alone. A non-zero managed
Chromium exit status is classified as `chromium_crash_or_oom` instead.

`LOGIN_INTERRUPTED` deliberately does not relaunch Chromium. Use the explicit
login retry command to create a new attempt. The `mimicgate_cleanup` initiator
marks expected stop/handoff callbacks and is not treated as an interruption.

Supported target:

- Local Linux, Python 3.11+
- DeepSeek Web and Qwen Web through Playwright Chromium or a configured Chrome/Chromium executable

Operational checks:

- `python -m compileall src`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `/health` for process health
- `/ready` for browser/provider readiness

Known fragile areas:

- DeepSeek DOM selectors and marker extraction
- Login challenges, CAPTCHA, and account verification
- Long-running browser profile state

Versioning:

- Milestone 5 is still a local-tool release level, not production SaaS hardening.
