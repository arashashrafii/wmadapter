# Web Model Adapter — Web-to-API Gateway for AI Agents

Web Model Adapter is a local OpenAI-compatible gateway for authenticated DeepSeek Web
and Qwen Web sessions. It drives provider web pages through a user-owned
browser profile and exposes the verified HTTP boundary to OpenCode, Hermes,
OpenClaw, and compatible clients. It does not use the paid DeepSeek API.
The project command, import namespace, configuration paths, and environment
variables use the `wmadapter` name.

The provider side is Web-only: DeepSeek and Qwen are accessed through their
browser chat pages.

## Gateway V1 / Provider Contract V2

Verified capabilities:

- FastAPI local server
- `/health`
- `/v1/models`
- `/v1/chat/completions` (ordinary responses and buffered SSE)
- `/v1/completions` (supported legacy text-completion subset)
- `/v1/embeddings` (contract validation; vectors are not currently supported)
- OpenAI-compatible chat requests, buffered SSE, model capabilities, and
  preservation of tools and tool results through the provider contract.
- DeepSeek/Qwen provider routing; Qwen is text-only and DeepSeek image input is
  not advertised until live model/UI verification establishes observable vision
  support.
- Managed browser sessions with headed login, canonical profile ownership,
  headed-to-headless handoff, session probing, profile locking, page ownership,
  configurable page caps, idle cleanup, and protected in-flight pages.
- CDP attach as a separate ownership path that never closes the user's browser.
- Loopback defaults, optional bearer authentication, readiness/status output,
  redacted logs, isolated browser sessions, and YAML configuration.

The gateway depends on authenticated sessions and the providers' current web
interfaces. CAPTCHA interaction is user-driven. Selectors, session validity,
upstream throttling, and provider availability can change without notice.

## Web Model Adapter installer

Interactive setup:

```bash
./install.sh
```

The installer sets up a local Python environment, lets you choose DeepSeek Web or Qwen Web, opens system Google Chrome for manual authentication, runs a smoke test, and prints the local OpenAI-compatible API URL. If Google Chrome is unavailable, it stops and asks the user to install it. No paid API key is required.

The installer uses one browser process only: the installed system Google Chrome.
It does not download Playwright's separate browser binary. Playwright is used
only as the Python library that controls the already-running Chrome session.
It prints a command that starts local Chrome with an isolated profile and a
loopback-only debugging endpoint. Complete login in that Chrome window and
leave it open while Web Model Adapter runs. Web Model Adapter attaches to that session; it does
not launch a second browser or attempt to bypass the site's CAPTCHA.
On Linux, the background service uses Xvfb so Chrome has no visible window while
retaining the headed browser behavior required by some providers. Install Xvfb
before running the installer if it is not already present.

Browser selection is explicit in config.yaml: browser.mode: managed (the
default) uses Web Model Adapter's persistent profile, while browser.mode: cdp attaches
to a user-launched Chrome configured by browser.cdp_endpoint. Existing
configurations that set cdp_endpoint without mode are interpreted as cdp for
backward compatibility. In Stage 1 this is only the configuration and
migration contract; it does not yet change runtime browser selection.

Managed authentication uses a sequential handoff on the same canonical
profile and executable: the headed login context is stopped and its exclusive
profile lock is released before the headless runtime starts. The headless
context probes the authenticated session after launch. If launch or the probe
fails, Web Model Adapter stops the failed context, restores headed mode on the same
profile where possible, and reports the handoff failure. CDP mode remains a
separate operator-selected attach path and does not use this handoff or close
the user's browser.

The local installer creates a user-level systemd service named
`wmadapter.service` for compatibility. Remove the local installation
with `./uninstall.sh`; systemd teardown is bounded and best-effort, so failures
are reported while safe local cleanup continues.

```bash
./uninstall.sh
```

Web Model Adapter accepts standard OpenAI-compatible requests from local clients. The
provider is a free Web Chat adapter, not the paid DeepSeek API provider.

## Linux quick start

```bash
git clone https://github.com/arashashrafii/wmadapter.git
cd wmadapter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
# Install Google Chrome from https://www.google.com/chrome/ if it is not already present.
# Web Model Adapter uses the system Google Chrome binary and does not install a browser.
cp config.example.yaml config.yaml
```

For DeepSeek Web:

```bash
./.venv/bin/wmadapter auth deepseek
```

Complete login in the opened browser. No DeepSeek API key is required.

Then start the bridge:

```bash
.venv/bin/wmadapter
```

The server defaults to `http://127.0.0.1:11555`.

Container packaging is temporarily unavailable. Use the local virtual
environment and optional user-level systemd service described here.

Authentication is browser-only. Web Model Adapter never accepts, stores, or
automates provider usernames or passwords; only the isolated Chrome profile
holds browser-managed session state.

Canonical environment variables use the `WMADAPTER_*` prefix, including
`WMADAPTER_CONFIG`, `WMADAPTER_LOGIN`, `WMADAPTER_XVFB`, and `WMADAPTER_URL`.

## Qwen authentication

Create a persistent Qwen browser session with manual or Google authentication:

```bash
.venv/bin/wmadapter auth qwen --google
```

Complete Google authentication in the opened browser, then press Enter in the terminal. The Qwen profile is stored under `.wmadapter-profile/qwen`.

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

Web Model Adapter's Web adapters depend on website DOM and authentication behavior. Tool-call
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
