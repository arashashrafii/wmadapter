# WebBridgeFreeRide

WebBridgeFreeRide is a local OpenAI-compatible gateway for free DeepSeek Web and Qwen Web sessions.

The provider side is Web-only: DeepSeek and Qwen are accessed through their
browser chat pages. The local OpenAI-compatible boundary exists for clients
such as OpenCode, Hermes and OpenClaw; this project does not use the paid DeepSeek API.

## Gateway V1 / Provider Contract V2

Implemented:

- FastAPI local server
- `/health`
- `/v1/models`
- `/v1/chat/completions` (ordinary responses and buffered SSE)
- Structured Provider Contract V2 with legacy complete(prompt) compatibility
- Standard request errors, model capabilities and tool-result round trips
- DeepSeek Web browser sessions with per-OpenClaw-session pages
- Text-to-structured tool-call simulation for OpenClaw
- Optional Qwen Web browser adapter

Milestones 2-5 add local-tool hardening, streaming-compatible responses, provider routing foundations, Docker packaging, and public maintenance/security docs.

## Milestone status

- M1: DeepSeek browser-backed non-streaming chat completion validated.
- M2: Stable local tool foundation: config validation, encrypted local credentials, redacted logs, readiness, retries.
- M3: Agent/OpenAI client compatibility base: optional OpenAI fields, SSE streaming shape, conversation IDs.
- M4: Provider routing foundation and adapter contract.
- M5: Docker/package metadata plus security and maintenance docs.

## FreeRide v3 installer

Interactive setup:

```bash
./install.sh
```

The installer sets up a local Python environment, lets you choose DeepSeek Web or Qwen Web, opens a browser for manual authentication, runs a smoke test, and prints the local OpenAI-compatible API URL. If Chrome or Chromium is unavailable, it offers to install the system Chromium package on supported Linux distributions. No paid API key is required.

The installer recommends `Launch Chromium yourself for login and service use?`.
This is the lightest reliable path for WebChat sign-in: it uses the installed
system Chromium and does not download Playwright's separate Chromium binary.
It prints a command that starts local Chromium with an isolated profile and a
loopback-only debugging endpoint. Complete login in that Chromium window and
leave it open while WebBridge runs. WebBridge attaches to that session; it does
not launch a second browser or attempt to bypass the site's CAPTCHA.

The local installer creates a user-level systemd service named `webbridgefreeride.service`. Remove the local installation with:

```bash
./uninstall.sh
```

When Qwen is selected and OpenClaw is installed, the installer can enable a
small cleanup plugin for the browser adapter:

```bash
openclaw plugins install --link "$PWD/openclaw-plugin" --force
openclaw plugins enable webbridgefreeride-openclaw
```

Each OpenClaw session maps to a separate browser conversation. When the
OpenClaw cleanup plugin receives a session-deleted event, WebBridge uses the
DeepSeek Web UI to delete the matching remote conversation before closing its
local browser page. Deleting all OpenClaw sessions therefore deletes each
matching DeepSeek conversation; unrelated DeepSeek conversations are never
selected.

For OpenClaw, configure the model as `webbridge/deepseek-chat`. The bridge
passes OpenClaw's tool definitions to DeepSeek Web in a strict text protocol and
converts a valid `<tool_call>...</tool_call>` response into an OpenAI-compatible
`tool_calls` message. OpenClaw executes the tool and sends the result back on
the next turn.

OpenClaw configuration follows its custom-provider format:

```json5
{
  models: { providers: { webbridge: {
    baseUrl: "http://127.0.0.1:11555/v1",
    apiKey: "local-webbridge",
    api: "openai-completions",
    models: [{ id: "deepseek-chat", name: "WebBridge DeepSeek Web",
      reasoning: false, input: ["text"] }]
  } } },
  agents: { defaults: { model: { primary: "webbridge/deepseek-chat" } } }
}
```

This provider is intentionally a free Web Chat adapter, not the paid DeepSeek
API provider.

## Linux quick start

```bash
git clone https://github.com/arashashrafii/webbridgefreeride.git
cd webbridgefreeride
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
playwright install chromium
cp config.example.yaml config.yaml
```

For DeepSeek Web:

```bash
./.venv/bin/python -m webbridgefreeride auth deepseek
```

Complete login in the opened browser. No DeepSeek API key is required.

Then start the bridge:

```bash
.venv/bin/python -m webbridgefreeride
```

The server defaults to `http://127.0.0.1:11555`.

For automatic login recovery without storing secrets in `config.yaml`, save encrypted local credentials outside the repository:

```bash
.venv/bin/python -m webbridgefreeride credentials set
```

The credential key is stored under `~/.config/webbridgefreeride/` and the encrypted credential file under `~/.local/share/webbridgefreeride/` by default.

## Qwen authentication

Create a persistent Qwen browser session with manual or Google authentication:

```bash
.venv/bin/python -m webbridgefreeride auth qwen --google
```

Complete Google authentication in the opened browser, then press Enter in the terminal. The Qwen profile is stored under `.webbridge-profile/qwen`.

## Test

Health check:

```bash
curl http://127.0.0.1:11555/health
```

Readiness check:

```bash
curl http://127.0.0.1:11555/ready
```

Chat request:

```bash
curl http://127.0.0.1:11555/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "Reply only with: OK"}]
  }'
```

## Important limitations

The Web adapters depend on website DOM and authentication behavior. Tool-call
simulation is deliberately allowlisted and the calling agent executes returned
tools; unresolved tool markers trigger bounded recovery and then a provider error.

The first successful live run on a real DeepSeek account is still required to validate the current selectors against the live site.


Streaming request:

```bash
curl http://127.0.0.1:11555/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-chat",
    "stream": true,
    "messages": [{"role": "user", "content": "Reply only with: OK"}]
  }'
```

Docker:

```bash
docker compose up --build
```

Security and maintenance notes live in `docs/SECURITY.md` and `docs/MAINTENANCE.md`.

For the complete maintainer handoff, verified behavior, extension status, and
the next-agent prompt, see `docs/HANDOFF.md`.


## V2 migration and compatibility

See [architecture](docs/ARCHITECTURE.md), [audit and migration notes](docs/MIGRATION_V2.md)
and [client verification/setup](docs/CLIENT_COMPATIBILITY.md). The original
complete/stream_complete methods and session cleanup routes remain available.
SSE is buffered; tools are emulated, not native. Context/output limits and usage
are unknown. Legacy sampling, token-limit and structured-output request fields
are accepted but not enforced by the browser. deepseek-reasoner is an alias,
not proof that the Web UI selected a reasoning model. Unknown HTTP model names
now return 404 instead of silently falling back to the default provider.

106 unit/contract tests and real client SDK transport checks passed against a
fixture provider. Current live WebChat behavior and full agent runs remain
unverified by this migration. No MCP dependency or GPT/OX placeholder was added.
Incoming OpenAI messages are converted into a provider-independent canonical
contract before DeepSeek or Qwen adapters are called. Optional bearer
authentication is enabled with `server.api_key` in `config.yaml`.
