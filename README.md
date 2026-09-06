# MimicGate — Web-to-API Gateway for AI Agents

MimicGate is a local OpenAI-compatible gateway for authenticated DeepSeek Web
and Qwen Web sessions. It drives provider web pages through a user-owned
browser profile and exposes the verified HTTP boundary to OpenCode, Hermes,
OpenClaw, and compatible clients. It does not use the paid DeepSeek API.
The historical `mimicgate` command and import namespace, configuration
paths, and environment variables remain supported compatibility aliases.

The provider side is Web-only: DeepSeek and Qwen are accessed through their
browser chat pages.

## Gateway V1 / Provider Contract V2

Verified capabilities:

- FastAPI local server
- `/health`
- `/v1/models`
- `/v1/chat/completions` (ordinary responses and buffered SSE)
- OpenAI-compatible chat requests, buffered SSE, model capabilities, and
  preservation of tools and tool results through the provider contract.
- DeepSeek data-URL image input and DeepSeek/Qwen provider routing.
- Managed browser sessions with headed login, canonical profile ownership,
  headed-to-headless handoff, session probing, profile locking, page ownership,
  configurable page caps, idle cleanup, and protected in-flight pages.
- CDP attach as a separate ownership path that never closes the user's browser.
- Loopback defaults, optional bearer authentication, readiness/status output,
  redacted logs, encrypted local credentials, and YAML configuration.

The gateway depends on authenticated sessions and the providers' current web
interfaces. CAPTCHA interaction is user-driven. Selectors, session validity,
upstream throttling, and provider availability can change without notice.

## MimicGate installer

Interactive setup:

```bash
./install.sh
```

The installer sets up a local Python environment, lets you choose DeepSeek Web or Qwen Web, opens a browser for manual authentication, runs a smoke test, and prints the local OpenAI-compatible API URL. If Chrome or Chromium is unavailable, it offers to install the system Chromium package on supported Linux distributions. No paid API key is required.

The installer uses one browser process only: the installed system Chromium.
It does not download Playwright's separate Chromium binary. Playwright is used
only as the Python library that controls the already-running Chromium session.
It prints a command that starts local Chromium with an isolated profile and a
loopback-only debugging endpoint. Complete login in that Chromium window and
leave it open while MimicGate runs. MimicGate attaches to that session; it does
not launch a second browser or attempt to bypass the site's CAPTCHA.

Browser selection is explicit in config.yaml: browser.mode: managed (the
default) uses MimicGate's persistent profile, while browser.mode: cdp attaches
to a user-launched Chromium configured by browser.cdp_endpoint. Existing
configurations that set cdp_endpoint without mode are interpreted as cdp for
backward compatibility. In Stage 1 this is only the configuration and
migration contract; it does not yet change runtime browser selection.

Managed authentication uses a sequential handoff on the same canonical
profile and executable: the headed login context is stopped and its exclusive
profile lock is released before the headless runtime starts. The headless
context probes the authenticated session after launch. If launch or the probe
fails, MimicGate stops the failed context, restores headed mode on the same
profile where possible, and reports the handoff failure. CDP mode remains a
separate operator-selected attach path and does not use this handoff or close
the user's browser.

The local installer creates a user-level systemd service named
`mimicgate.service` for compatibility. Remove the local installation
with `./uninstall.sh`; systemd teardown is bounded and best-effort, so failures
are reported while safe local cleanup continues.

```bash
./uninstall.sh
```

MimicGate accepts standard OpenAI-compatible requests from local clients. The
provider is a free Web Chat adapter, not the paid DeepSeek API provider.

## Linux quick start

```bash
git clone https://github.com/arashashrafii/mimicgate.git
cd mimicgate
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
# Install Chromium with your Linux package manager if it is not already present.
# Do not run `playwright install chromium`: MimicGate attaches to system Chromium.
cp config.example.yaml config.yaml
```

For DeepSeek Web:

```bash
./.venv/bin/mimicgate auth deepseek
```

Complete login in the opened browser. No DeepSeek API key is required.

Then start the bridge:

```bash
.venv/bin/mimicgate
```

The server defaults to `http://127.0.0.1:11555`.

Container packaging is temporarily unavailable. Use the local virtual
environment and optional user-level systemd service described here.

For automatic login recovery without storing secrets in `config.yaml`, save encrypted local credentials outside the repository:

```bash
.venv/bin/mimicgate credentials set
```

The credential key is stored under `~/.config/mimicgate/` and the encrypted credential file under `~/.local/share/mimicgate/` by default.

Canonical environment variables use the `MIMICGATE_*` prefix, including
`MIMICGATE_CONFIG`, `MIMICGATE_LOGIN`, `MIMICGATE_XVFB`, `MIMICGATE_KEY_FILE`,
`MIMICGATE_CREDENTIAL_FILE`, and `MIMICGATE_URL`. The historical
`MIMICGATE_*` names remain fallbacks; when both are set, the MimicGate name
takes precedence. Existing `mimicgate` profile, credential, log,
service, and plugin paths remain valid compatibility paths.

## Qwen authentication

Create a persistent Qwen browser session with manual or Google authentication:

```bash
.venv/bin/mimicgate auth qwen --google
```

Complete Google authentication in the opened browser, then press Enter in the terminal. The Qwen profile is stored under `.mimicgate-profile/qwen`.

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

MimicGate's Web adapters depend on website DOM and authentication behavior. Tool-call
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
