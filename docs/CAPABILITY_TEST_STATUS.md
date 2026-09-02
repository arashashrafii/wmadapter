# OpenClaw Capability Test Status

This is the ordered acceptance checklist for WebBridge FreeRide integration
with OpenClaw. Update this file after each capability is tested. Do not mark a
capability complete from a unit test alone when an OpenClaw end-to-end check is
required.

Status meanings:

- DONE: verified and evidence is recorded.
- PARTIAL: one part works, but the capability still has an unverified part.
- BLOCKED: a known external dependency prevents the test.
- TODO: not tested yet.

## Ordered status

| # | Capability | Status | Evidence / acceptance result |
|---:|---|---|---|
| 1 | System commands and process management (exec, process) | DONE | OpenClaw executed a command and polled a background process; final output was visible. |
| 2 | File operations (read, write, edit, apply_patch) | DONE | All four operations were verified on a temporary workspace file; 38 compatibility/unit tests pass. |
| 3 | Browser control and login | DONE | OpenClaw opened Atimode, read the page, entered the phone login flow, reached OTP, and the account page was later verified. |
| 4 | Web search and page retrieval | TODO | Must test the configured OpenClaw web/search tools and record provider/network limitations. |
| 5 | Images, audio, PDF, and media | TODO | Must test supported media tools with safe local fixtures. |
| 6 | Independent sessions and memory | TODO | Must prove session isolation and memory read/write behavior. |
| 7 | Multiple agents and delegation | TODO | Must create/delegate to an agent and verify the returned result. |
| 8 | Skills and persistent workflows | TODO | Must invoke a matching skill and verify its workflow is followed. |
| 9 | Plugins and added capabilities | TODO | Must verify plugin discovery/activation and one plugin-provided capability. |
| 10 | Scheduling (cron, heartbeat, automation) | TODO | Must create a harmless test schedule, observe one execution, then clean it up. |
| 11 | Messaging channels | TODO | Must test only configured/authorized channels; record unavailable channels separately. |
| 12 | Mobile/node camera, screen, and voice | TODO | Requires a paired node; currently no paired node is recorded. |
| 13 | Access control, sandbox, and command approval | TODO | Must verify policy boundaries and approval behavior without weakening security. |

## Execution protocol

1. Test only the next TODO item.
2. Use the official OpenClaw surface and keep WebBridge Web-only.
3. Record the exact command/prompt, result, failure, and relevant commit.
4. Update this checklist and add a regression test or fixture when practical.
5. Stop and report blockers; do not silently skip to a later item.
6. Obtain confirmation before destructive actions, external messages, uploads,
   account changes, or persistent schedule creation.

## Current known environment

- WebBridge service is a user-level systemd service.
- Current runtime endpoint is usually http://127.0.0.1:11556; checked-in
  examples default to port 11555.
- DeepSeek Web is the active provider in the latest verification.
- Qwen is configured but was not the active provider in the latest verification.
- OpenClaw's managed browser login works through its browser CLI.
- The Chrome extension copy is installed at
  /home/arash/.openclaw/browser/chrome-extension; manual loading into the
  user's Chrome remains a separate pending setup.
- OpenClaw CLI/Gateway versions must be kept aligned; see docs/HANDOFF.md.

## Evidence index

- Capability 1: OpenClaw session keys step01-final-check-20260902 and
  step01-exec-process-fixed-20260902.
- Capability 2: OpenClaw session keys step02-write-20260902,
  step02-read-20260902, step02-edit-20260902, and
  step02-patch-retry-20260902.
- Capability 3: OpenClaw browser login and Atimode account-page verification
  performed on 2026-09-02.
- Unit contract coverage: tests/test_openclaw_compat.py.
