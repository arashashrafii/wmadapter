# Web Model Adapter

Web Model Adapter is a local OpenAI-compatible gateway for browser-backed AI
providers. It exposes authenticated DeepSeek Web and Qwen Web sessions to
OpenClaw, OpenCode, and other compatible clients. It does not use provider API
keys or the paid DeepSeek API.

## Quick start

```bash
git clone https://github.com/arashashrafii/wmadapter.git
cd wmadapter
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
```

Install Google Chrome and Xvfb on Linux if needed. WMAdapter uses the system
Chrome binary and isolated persistent browser profiles.

Start the product server:

```bash
wmadapter --config config.yaml
```

The product API is `http://127.0.0.1:11555/v1`.

## Provider authentication and discovery

Provider availability is determined by the live browser session. You do not
need to enable a provider, add it to an allowlist, or select a default provider
before using it:

```bash
wmadapter --config config.yaml login qwen
wmadapter --config config.yaml login deepseek
wmadapter --config config.yaml doctor
```

`/ready` reports each provider independently. `/v1/models` advertises only
models belonging to providers whose browser session is currently ready. The
configured model catalog is a declaration of known IDs, not proof of login.

See [Provider discovery and readiness](docs/PROVIDER_DISCOVERY.md).

## OpenClaw

Generate OpenClaw configuration from the same config used to run the server:

```bash
wmadapter --config config.yaml run openclaw qwen
openclaw gateway restart
```

The endpoint is derived from `server.host` and `server.port`. For the isolated
test environment:

```bash
wmadapter --config config.test.yaml run openclaw qwen
```

The test endpoint is `http://127.0.0.1:11556/v1`. Do not mix product and test
endpoints or browser profiles.

Common Qwen model IDs are:

```text
wmadapter/qwen-chat
wmadapter/qwen3.7-plus
wmadapter/qwen3.8-max
wmadapter/qwen3.8-omni-flash
```

Verify the model is live before testing OpenClaw:

```bash
curl http://127.0.0.1:11555/health
curl http://127.0.0.1:11555/ready
curl http://127.0.0.1:11555/v1/models
```

The requested model must appear in `/v1/models`; a model listed only in
`openclaw.json` is not enough.

## DeepSeek Harness and Pi

The same provider/model catalog can be added to DeepSeek Harness (`dsh`) or
Pi. Both commands preserve existing client settings and accept `--config` for
an alternate destination:

```bash
wmadapter --config config.yaml run dsh qwen
wmadapter --config config.yaml run pi qwen
```

By default, DSH writes `$DSH_HOME/profiles/web/cordis.patch.yml` (or
`~/.dsh/profiles/web/cordis.patch.yml`) and Pi writes `~/.pi/agent/models.json`.
Both clients are configured for WM Adapter's OpenAI-compatible `/v1` endpoint;
the selected provider's configured model IDs are written to the client.

## Test environment

```bash
npm run start:test
wmadapter --config config.test.yaml login qwen
wmadapter --config config.test.yaml doctor
curl http://127.0.0.1:11556/v1/models
```

The test environment uses port `11556`, separate profiles, and separate logs.
Never use the product configuration or profile for automated tests.

## API surface

- `GET /health`
- `GET /ready`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/completions` (supported legacy subset)
- `POST /v1/responses` (non-streaming text subset)
- `POST /v1/images` for verified Qwen image generation

Unsupported capabilities return explicit errors. Streaming is buffered because
the upstream web providers do not expose native token streaming.

### Long-running requests

Browser-backed providers can take several minutes while rendering a large
answer or building files. Streaming chat requests stay alive with heartbeat
chunks and do not use a shorter gateway timeout than the provider adapter. If
a client has its own shorter HTTP timeout, send `X-WMAdapter-Async: true` and
an `Idempotency-Key`:

```http
POST /v1/chat/completions
X-WMAdapter-Async: true
Idempotency-Key: website-build-42
```

The gateway returns `202` with a request ID and `poll_url` while the provider
continues in the background. Poll that URL, or repeat the same request with
the same idempotency key, to observe the existing work without submitting the
browser action twice. Completed jobs are retained for the configured
`server.async_job_retention_ms` period.

## CLI reference

```bash
wmadapter --config config.yaml check
wmadapter --config config.yaml check ready
wmadapter --config config.yaml check ready qwen
wmadapter --config config.yaml doctor
wmadapter --config config.yaml doctor --fix
wmadapter --config config.yaml run opencode qwen
wmadapter --config config.yaml run openclaw qwen
```

Provider `enable`, `disable`, and `default` commands remain accepted for old
configuration files, but are no longer required for discovery or explicit
model routing.

After a successful login, the provider is enabled automatically unless it was
explicitly disabled with the CLI. If a provider requires a proxy, configure it
before login:

```bash
wmadapter proxy add qwen http://localhost:8080
wmadapter login qwen
```

## Security and limitations

Authentication is browser-only. Credentials and raw cookies remain in the
isolated browser profile and are not stored by the gateway. Do not commit
`config.yaml`, profiles, logs, or credentials.

The adapters depend on live provider websites, DOM structure, account access,
CAPTCHA state, quotas, and regional availability. CAPTCHA and interactive
authentication remain user-driven.

More detail: [Architecture](docs/ARCHITECTURE.md), [Security](docs/SECURITY.md),
[Maintenance](docs/MAINTENANCE.md), and [Provider discovery](docs/PROVIDER_DISCOVERY.md).

## Development

```bash
.venv/bin/pytest -q
```

Use `config.test.yaml` for manual service checks. Do not run the product server
as part of automated tests.
