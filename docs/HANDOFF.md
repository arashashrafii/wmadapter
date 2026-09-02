# WebBridge FreeRide Handoff

This document records the current implementation, verified behavior, known
limitations, and next work for a new maintainer.

## Project purpose

WebBridge FreeRide is a local, Web-only adapter for free DeepSeek Web and Qwen
Web access. It exposes a local OpenAI-compatible boundary so OpenClaw can keep
owning agents, sessions, tools, skills, plugins, channels, and automation.
It is not the paid DeepSeek API.

## Current implementation

- FastAPI server with /health, /ready, /v1/models, and /v1/chat/completions.
- DeepSeek Web browser adapter using Playwright and a persistent profile.
- Optional Qwen Web adapter with manual/Google authentication.
- One provider page per OpenClaw session key.
- OpenAI-compatible non-streaming and SSE response shapes.
- Text protocol for tool calls using the tool_call marker.
- Parsing support for XML markers, legacy invoke markers, Markdown JSON, and
  renderer-stripped JSON.
- Allowlisted tool names only; OpenClaw executes the returned tools.
- Tool results and assistant tool calls are preserved in the next prompt.
- Empty post-tool answers receive a safe fallback instead of a blank response.
- DeepSeek Web renderer labels (text, Copy, Download) are removed from final
  visible text.
- A narrow compatibility fix normalizes only malformed
  browser/action=act/kind=fill arguments from top-level ref/text into
  OpenClaw's required fields array.
- User-level systemd service support in install.sh and uninstall.sh.
- Optional openclaw-plugin cleanup plugin for releasing local pages when
  OpenClaw sessions are deleted.

## Verified tests

Run from the repository:

    .venv/bin/python -m unittest discover -s tests -q
    .venv/bin/python -m compileall -q src
    git diff --check

At handoff, all 38 unit tests pass. The service is installed and normally
listens on 127.0.0.1:11556 in the current user environment; the checked-in
example defaults to 11555.

Verified through OpenClaw:

- exec, process, read, write, edit, and apply_patch.
- Browser navigation and title extraction (Atimode).
- Atimode phone login through OpenClaw browser CLI reached OTP and the account
  page was then verified.
- The current OpenClaw browser profile can open pages and inspect snapshots.

## Browser extension status

The current OpenClaw installation is version 2026.8.1 when invoked with:

    /usr/local/bin/node /home/arash/.npm-global/lib/node_modules/openclaw/openclaw.mjs

The shell PATH points to an older 2026.5.28 CLI using Node v24.13.0.
The Gateway uses /usr/local/bin/node (v24.18.1). Use the explicit command
above until PATH and Node versions are unified.

Native host preparation is complete:

- Extension copy: /home/arash/.openclaw/browser/chrome-extension
- Chrome native host registration: owned.
- Chrome Store extension: not detected.
- Manual setup is still required: load the unpacked extension from the path
  above through chrome://extensions with Developer mode enabled.

The official flow is to run openclaw browser extension install, then install
the Store extension or load the unpacked copy. Keep the Gateway running.

## Known issues

- OpenClaw Gateway and shell CLI were previously version-mismatched. Running
  doctor --fix with the Gateway's Node restored a valid config, but the running
  Gateway may later add fields unknown to the older CLI. Unify the installation
  before further config work.
- OpenClaw has warnings for an unavailable optional DuckDuckGo plugin.
- The end-to-end agent-driven login workflow sometimes stops or hangs after a
  browser action; direct OpenClaw browser CLI actions work reliably.
- OTP must always be entered by the user.
- Web selectors and response extraction depend on changes to DeepSeek/Qwen UI.
- Qwen was not the active provider in the last verification.

## Safe next step

1. Finish loading the unpacked OpenClaw extension in the user's Chrome.
2. Run openclaw browser extension status --json with the matching CLI.
3. Configure an existing-session Chrome profile only after the extension is
   connected, then test a harmless page title read.
4. Re-run the full unit suite and add an end-to-end regression for browser
   fill with the exact OpenClaw fields shape.

## Prompt for the next agent

Continue WebBridge FreeRide from the latest GitHub commit. Read README.md,
docs/HANDOFF.md, docs/ARCHITECTURE.md, and the current tests before changing
code. Preserve the Web-only design: DeepSeek/Qwen must use their browser Web
chats, while OpenClaw remains responsible for agents, sessions, tools, skills,
plugins, channels, and automation. Do not replace this with a paid model API.

First inspect git status, validate the service, and run the 38-test suite.
Then finish the OpenClaw Chrome extension setup using the matching OpenClaw
CLI/Gateway Node version. The native host is already registered and the
unpacked extension is at /home/arash/.openclaw/browser/chrome-extension; do
not delete user data or log out accounts without confirmation. Verify the
extension with a harmless page-title test. Keep the narrow browser-fill
normalization isolated to browser + act + fill, and add tests before any
broader protocol changes. Report exact files, tests, and remaining blockers.
