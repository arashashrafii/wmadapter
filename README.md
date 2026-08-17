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

Streaming and production hardening are not part of Milestone 1.

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

The server defaults to `http://127.0.0.1:8000`.

## Test

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Chat request:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "Reply only with: OK"}]
  }'
```

## Important limitations

This depends on DeepSeek's current website DOM and authentication flow. A DeepSeek UI change, CAPTCHA, verification challenge, or service policy change can break the bridge. The selectors are isolated in `src/webbridgefreeride/providers/deepseek/selectors.py` to make repairs easier.

The first successful live run on a real DeepSeek account is still required to validate the current selectors against the live site.
