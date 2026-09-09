# Maintenance Policy

## Browser login interruption diagnostics

Inspect provider `lifecycle_events` when a managed login ends unexpectedly.
The final event identifies the boundary through `reason`: `user_close`,
`page_crash`, `context_close`, `playwright_disconnect`,
`display_session_failure`, `chromium_crash_or_oom`, or managed cleanup. Events include the login attempt
ID, browser generation, page count, authentication state, Chromium PID, and an
exit status when Playwright exposes the process.

`playwright_disconnect` means transport evidence alone. A non-zero managed
Chromium exit status is classified as `chromium_crash_or_oom` instead.

Use the following taxonomy when triaging a failed headed login:

- `user_close`: the operator closed the login page/window.
- `page_crash`: Playwright reported a page crash.
- `context_close`: the browser context ended without an expected cleanup.
- `playwright_disconnect`: the Playwright transport disconnected without process-exit evidence.
- `chromium_crash_or_oom`: managed Chromium exited with a non-zero status.
- `display_session_failure`: the headed browser could not be launched or displayed.
- `wmadapter_cleanup`: expected stop or headed-to-headless handoff, recorded as the event initiator.

The headed-to-headless handoff records `reason=handoff` and closes the headed
context once before opening the same provider profile headlessly. Cleanup
callbacks must never be reported as `user_close`.

`LOGIN_INTERRUPTED` deliberately does not relaunch Chromium. Use the explicit
login retry command to create a new attempt. The `wmadapter_cleanup` initiator
marks expected stop/handoff callbacks and is not treated as an interruption.

Supported target:

- Local Linux, Python 3.11+
- DeepSeek Web and Qwen Web through Playwright Chromium or a configured Chrome/Chromium executable

Operational checks:

- `python -m compileall src`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `/health` for process health
- `/ready` for browser/provider readiness

Installer checks:

- `install.sh` changes into the repository directory before creating generated
  files, resolves an installed Chrome/Chromium executable, and writes the same
  absolute executable path, provider URL, and profile selection used by manual
  authentication and the service.
- If no supported browser is found, the installer offers the host package
  manager's Chromium package. It does not install a second Playwright-managed
  browser.
- Manual authentication is always foreground and managed-mode only. Google
  sign-in falls back to an explicit manual click when no matching button is
  detected; CAPTCHA and credentials remain user-driven.
- A cancelled login or failed service/health step stops the user service and
  removes the generated unit file. Browser profile locks are released by the
  authentication cleanup path; existing profiles and source files are kept.

Known fragile areas:

- DeepSeek DOM selectors and marker extraction. The login probe accepts the
  current `Message DeepSeek` textarea and contenteditable/ARIA textbox variants.
  If readiness times out, manual authentication reports the last probe state,
  matched selector, URL, frame count, and per-selector count/visibility/editability
  diagnostics (or the absence of a visible editable chat input).
- Login challenges, CAPTCHA, and account verification
- Long-running browser profile state

Versioning:

- Milestone 5 is still a local-tool release level, not production SaaS hardening.
