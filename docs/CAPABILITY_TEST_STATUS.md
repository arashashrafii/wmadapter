# OpenClaw Capability Test Status

This is the ordered acceptance checklist for MimicGate integration
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
| 9 | Plugins and added capabilities | DONE | The OpenClaw `duckduckgo` plugin is installed, loaded, and selected as the enabled web-search provider. A Web UI agent test completed successfully using `web_search` with no tool failures and returned two Python.org links. |
| 10 | Scheduling (cron, heartbeat, automation) | DONE | OpenClaw 2026.8.1 executed a one-shot command job at its scheduled time (`TEST10_CRON_OK`); the delete-after-run job was disabled after success. Existing heartbeat and cron jobs also report successful last runs. Delivery was not requested for the test and Telegram fallback has no configured chatId. |
| 11 | Messaging channels | BLOCKED | Telegram is configured, but OpenClaw reports its account as `not-running/recovering` with `channel stop timed out after 5000ms`; no authorized destination was available for a real message test. Discord and Slack are not configured. |
| 12 | Mobile/node camera, screen, and voice | BLOCKED | OpenClaw `nodes status`, `nodes list`, and `nodes pending` all returned empty results; no paired or pending mobile/node is available for camera, screen, or voice testing. |
| 13 | Access control, sandbox, and command approval | PARTIAL | Read-only policy checks completed: approvals have no pending requests; effective exec policy is `security=full`, `ask=off`, `askFallback=deny`; main and test agent sandbox mode is `off` with channel/node tools denied. Security audit found one critical unallowlisted extension and warnings for unsandboxed runtime/filesystem access and missing trusted proxies. Interactive approval behavior remains unverified. |

## Execution protocol

1. Test only the next TODO item.
2. Use the official OpenClaw surface and keep MimicGate Web-only.
3. Record the exact command/prompt, result, failure, and relevant commit.
4. Update this checklist and add a regression test or fixture when practical.
5. Stop and report blockers; do not silently skip to a later item.
6. Obtain confirmation before destructive actions, external messages, uploads,
   account changes, or persistent schedule creation.

## Current known environment

- MimicGate service is a user-level systemd service.
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
- Capability 10: OpenClaw one-shot cron job `test10-one-shot-20260903` ran
  successfully on 2026-09-03 with output `TEST10_CRON_OK`; run history was
  verified and the one-shot job was disabled after execution.
- Capability 9: OpenClaw plugin inspection confirmed `duckduckgo` is loaded and
  selected by `tools.web.search.provider`. Session
  `agent:main:test-live-search-20260904` completed a live `web_search` call
  successfully and returned two Python.org links.
- Capability 11: `channels list --json` found only the configured Telegram
  account; `channels status --json` reported it as not running/recovering, so
  no external message was sent. Discord and Slack are unavailable.
- Capability 12: `nodes status`, `nodes list`, and `nodes pending` returned no
  nodes or pairing requests. The available camera and screen commands require
  a paired node, so media/node testing is blocked pending pairing.
- Capability 13: Read-only `approvals get/pending`, `exec-policy show`, and
  `sandbox explain --agent main` checks completed. No approval was pending;
  sandbox mode is off and the effective policy has `ask=off`. `security audit`
  reported one critical `plugins.allow` finding plus two warnings. No security
  remediation or policy change was applied.
- Unit contract coverage: tests/test_openclaw_compat.py.
