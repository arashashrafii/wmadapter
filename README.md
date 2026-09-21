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
- `/v1/images` (validated Qwen image generation with base64 artifact serialization when enabled by verified configuration)
- `/v1/images/edits` (validated contract; image understanding/editing is not currently verified or supported)
- `/v1/audio/*` and `/v1/realtime` (validated contracts; audio/realtime are not currently supported)
- `/v1/files` (validated contract; file/PDF handling is not currently supported)
- `/v1/batches` (validated contract; asynchronous batch processing is not currently supported)
- OpenAI-compatible chat requests, buffered SSE, model capabilities, and
  preservation of tools and tool results through the provider contract.
- DeepSeek/Qwen provider routing; Qwen image generation is advertised only when
  the verified configuration flag is enabled. DeepSeek image input is not
  advertised until live model/UI verification establishes observable vision
  support.
- Sampling controls are validated; shared Chat Completions and OpenClaw reject
  them, while OpenCode accepts positive `max_tokens` only as a client-requested
  budget. It is used for local context/headroom diagnostics and is never sent
  to or serialized for the web provider. Only `n=1` and streamed
  `stream_options.include_usage` are supported by the current web adapters.
- `/v1/models` identifies the backing web provider and reports gateway limits;
  provider context/output limits and usage remain unknown unless observed.
- Chat Completions and Responses return usage only when the provider supplies a
  complete, internally consistent token record; otherwise usage is `null`.
- Chat tools support validated function definitions and serial emulated calls;
  custom tools, parallel calls, and Responses tools are rejected explicitly.
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

Installation:

```bash
./install.sh
```

The installer sets up a local Python environment, installs the system service, and writes a provider-independent configuration without opening a login window. Provider authentication is performed afterward with the CLI, one provider at a time. If Google Chrome is unavailable, installation stops and asks the user to install it. No paid API key is required.

The installer uses the installed system Google Chrome and does not download
Playwright's separate browser binary. Playwright is used only as the Python
library that controls each provider's isolated persistent profile. Login is a
separate CLI operation after installation; Web Model Adapter does not automate
CAPTCHA or provider passwords.
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

The local installer creates a system-wide systemd service named
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

Provider configuration and login:

```bash
wmadapter provider list
wmadapter provider enable deepseek
wmadapter provider enable qwen
wmadapter provider default deepseek

# Proxies are stored separately for each provider
wmadapter add proxy qwen http://localhost:8080
wmadapter proxy list
wmadapter proxy remove qwen
wmadapter check ready

# Generate/update an OpenCode provider from the WM Adapter model catalog
wmadapter run opencode deepseek
wmadapter run opencode qwen
wmadapter run openclaw deepseek
wmadapter run openclaw qwen

wmadapter login deepseek
wmadapter login qwen --google
```

Each provider uses its own persistent Chrome profile. Browser-managed session state is reused on later starts; WM Adapter does not store provider passwords or raw cookies.
If configured, a provider proxy is used only by that provider's browser session and authentication flow.
The OpenCode command writes to `~/.config/opencode/opencode.json`, preserves existing entries, and adds all models known for the selected WM Adapter provider.
The OpenClaw command does the same in `~/.openclaw/openclaw.json` under `models.providers.wmadapter`.

Then start the bridge:

```bash
wmadapter
```

### اجرای جداگانه محصول و تست

محیط محصول و تست باید با فایل تنظیمات و پروفایل مرورگر جدا اجرا شوند. محصول روی
پورت `11555` و تست روی پورت `11556` است:

```bash
# محصول
.venv/bin/wmadapter --config config.yaml

# تست (در ترمینال جدا)
.venv/bin/wmadapter --config config.test.yaml
```

یا از اسکریپت‌های npm استفاده کنید: `npm run start:product` و
`npm run start:test`. فایل `config.test.yaml` از پروفایل و لاگ مستقل استفاده
می‌کند؛ بنابراین تغییرات و اجرای تست روی سرویس محصول اثر نمی‌گذارد. برای تست‌های
اتوماتیک نیز آدرس پایه `http://127.0.0.1:11556/v1` است.

The server defaults to `http://127.0.0.1:11555` (also available as
`http://localhost:11555`). OpenCode and OpenClaw can use the shared OpenAI-compatible
base URL `http://127.0.0.1:11555/v1`.

The installer creates a system-wide systemd service under `/etc/systemd/system`
and starts it with a health check, but does not require provider login. Run
`./install.sh` directly; it elevates itself with `sudo` when needed and may ask
for the password once. The `wmadapter login ...` command temporarily pauses
the service while the provider profile is authenticated, then starts the
service again automatically.

Authentication is browser-only. Web Model Adapter never accepts, stores, or
automates provider usernames or passwords; only the isolated Chrome profile
holds browser-managed session state.

## OpenCode and timeout semantics

Timeouts apply to one provider request, not to the whole OpenCode project run.
An OpenCode tool loop is a sequence of independent streaming
`/v1/opencode/chat/completions` requests: OpenCode sends a prompt plus the
conversation/tool history, Web Model Adapter submits that turn to DeepSeek Web,
waits for the rendered answer, and then returns the answer or tool call. The
next tool result starts a new request. Therefore a project run with 20 turns can
take substantially longer than 15 minutes; there is no global project-run
timeout in Web Model Adapter.

For DeepSeek, `deepseek.timeout_ms` is the base timeout for a single Web Chat
turn (180 seconds by default). Long OpenCode histories receive an adaptive
extension: 30 seconds for each additional 8,000 prompt characters after the
first 12,000 characters, capped at 900,000 ms (15 minutes). For example, an
87,000-character turn receives approximately 480 seconds. This is an
observation timeout for the browser-rendered answer; it does not resend a
message after the send gesture.

The OpenCode streaming route has a separate 900-second watchdog. It bounds how
long the HTTP request remains open and currently matches the maximum DeepSeek
turn timeout. If either limit expires after submission, the result is treated
as uncertain because DeepSeek may still have received or be processing the
message. The adapter then performs a bounded, read-only reconciliation of the
same conversation (120 seconds by default). If the late answer appears, it is
returned to OpenCode and the tool loop can continue. Automatic replay remains
disabled when reconciliation cannot establish the result, avoiding duplicate
tool actions. A timeout in one turn does not represent the total time already
spent on earlier turns.

Canonical environment variables use the `WMADAPTER_*` prefix, including
`WMADAPTER_CONFIG`, `WMADAPTER_LOGIN`, `WMADAPTER_XVFB`, and `WMADAPTER_URL`.

## Qwen authentication

Create a persistent Qwen browser session with manual or Google authentication:

```bash
.venv/bin/wmadapter login qwen --google
```

Complete Google authentication in the opened browser. The Qwen profile is stored under `~/.local/share/wmadapter/profiles/qwen` by default.

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
SSE is buffered; tools are emulated, not native. Provider context/output limits
and usage are unknown unless observed from the provider. Unsupported sampling
and token-limit controls are rejected; only `n=1` and streamed
`stream_options.include_usage` are supported. `deepseek-reasoner` is an alias,
not proof that the Web UI selected a reasoning model. Unknown HTTP model names
now return 404 instead of silently falling back to the default provider.

## Issue #52 compatibility matrix

Supported and verified at the local contract level:

- `GET /health`, `GET /ready`, `GET /props`, and `GET /v1/models` report
  provider identity, capabilities, and known gateway limits without inventing
  upstream limits.
- `POST /v1/chat/completions` supports text, message/tool history, serial
  emulated function tools, validated tool choice, ordinary responses, and
  buffered SSE.
- `POST /v1/completions` supports the documented legacy text subset and
  `POST /v1/responses` supports non-streaming text responses.
- `/v1/chat/completions` is the shared OpenAI-compatible route. Positive
  `max_tokens` and `max_completion_tokens` values are accepted as client-only
  budgets and are never forwarded to the web provider. The OpenCode-specific
  route remains available for compatibility, but clients do not need it.
  Clients execute returned tools and send results back.

Validated but explicitly unsupported by the current web providers:

- `/v1/embeddings`, `/v1/images/edits`, `/v1/audio/*`, `/v1/realtime`, `/v1/files`, `/v1/videos`,
  and `/v1/batches` return safe `501` capability errors after validation.
- Custom tools, parallel execution, deterministic sampling/token controls,
  audio/video/file/PDF input, and image input are rejected where applicable.
  DeepSeek vision and Qwen image input are not claimed without live UI/model
  evidence. Qwen image generation returns one validated PNG/JPEG/GIF/WebP
  artifact as `b64_json` when `qwen.image_generation_verified` is enabled;
  otherwise it returns `image_generation_unverified` and no data.

Deterministic OpenCode/OpenClaw acceptance coverage:

```bash
PYTHONPATH=src:tests .venv/bin/python -m unittest \
  tests.test_opencode_channel tests.test_http_contract
```

The opt-in live suite requires manual authentication and never automates
credentials:

```bash
WMADAPTER_LIVE_COMPAT=1 \
PYTHONPATH=src:tests .venv/bin/python -m tests.live_compatibility \
  --confirm-live --group golden --format markdown --output live-compatibility-report.md
```

Treat live media cases as capability checks, not vision evidence. Do not
commit reports containing credentials, browser session data, prompts, or
provider transcripts.

Recovery is fail-closed and bounded to one repair request. Repairs use the
provider's isolated `repair_complete` API when available. Legacy providers use
a unique `repair:` conversation ID as a safe fallback; repair output is not
appended to the primary conversation, and the original ID remains active for
the next continuation. Its diagnostics are
redacted to reason codes, lengths, hashes, and outcomes; streamed provider
failures remain distinct from `protocol_recovery_failed`.

Prompt budgets are configurable with `limits.context_budget_chars` and
`limits.context_budget_profiles` (exact model or provider keys). Small prompts
remain unchanged; oversized histories receive one pre-submit state-ledger and
recent-window compaction, while an oversized current message is rejected with
`context_length_exceeded`. OpenCode's positive `max_tokens` is diagnostic-only;
zero, negative, non-integer, and conflicting values remain 400 errors. Unknown
`max_output_tokens` remains metadata-only when provider limits are unknown. No
post-submit replay occurs. `/health` is process health; `/ready` is
authenticated provider readiness and can return 503.

106 unit/contract tests and real client SDK transport checks passed against a
fixture provider. Current live WebChat behavior and full agent runs remain
unverified by this migration. No MCP dependency or GPT/OX placeholder was added.
Incoming OpenAI messages are converted into a provider-independent canonical
contract before DeepSeek or Qwen adapters are called. Optional bearer
authentication is enabled with `server.api_key` in `config.yaml`.
