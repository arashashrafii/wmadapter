# WebBridgeFreeRide

WebBridgeFreeRide is a proof of concept for a local OpenAI-compatible gateway that uses browser automation to talk to DeepSeek Web.

## Milestone 1 scope

Implemented:

- FastAPI local server
- `/health`
- `/v1/models`
- `/v1/chat/completions` (non-streaming)
- Playwright Chromium with a persistent profile
- DeepSeek login/session detection
- Optional first-login credentials via environment variables
- DeepSeek prompt submission
- DOM response extraction

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

The installer asks whether to use Docker or the current OS, lets you choose a free chatbot target (`deepseek`, `kimi`, `glm`, `qwen`), configures credential or URL/manual authentication, runs a smoke test, and prints the local OpenAI-compatible API URL. The current implemented runtime adapter is DeepSeek; other provider choices are saved as configuration for future adapters.

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

For the first login, either log into DeepSeek manually in the Chromium window, or temporarily export credentials in the shell:

```bash
export DEEPSEEK_EMAIL='your-email'
export DEEPSEEK_PASSWORD='your-password'
```

Then start the bridge:

```bash
python -m webbridgefreeride
```

The server defaults to `http://127.0.0.1:11555`.

For automatic login recovery without storing secrets in `config.yaml`, save encrypted local credentials outside the repository:

```bash
python -m webbridgefreeride credentials set
```

The credential key is stored under `~/.config/webbridgefreeride/` and the encrypted credential file under `~/.local/share/webbridgefreeride/` by default.

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

This depends on DeepSeek's current website DOM and authentication flow. A DeepSeek UI change, CAPTCHA, verification challenge, or service policy change can break the bridge. The selectors are isolated in `src/webbridgefreeride/providers/deepseek/selectors.py` to make repairs easier.

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
