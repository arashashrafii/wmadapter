# Client compatibility verification

Verified 2026-09-05 against the V2 HTTP application with a deterministic text
provider. No browser login, paid endpoint, user tools or cleanup operation was
used. These checks establish transport compatibility, not Web model quality.

## Results

- 106 unittest tests pass: original 86 plus provider and HTTP contract tests.
  They cover legacy methods/imports, DeepSeek attachment dispatch, Qwen session
  preservation, partial-output timeout errors, tool/result IDs, required/named/
  none choice, malformed requests, errors, model registry, SSE and local titles.
- OpenAI Python 2.24.0 installed in Hermes' environment: nonstream tool call,
  result-message continuation and streamed text passed. This is Hermes' SDK,
  not an end-to-end run of the Hermes agent or its context-compaction logic.
- @openclaw/ai 2026.8.1 installed locally: actual completeSimple transport
  accepted streamed tool calls and the following tool-result turn.
- @ai-sdk/openai-compatible 3.0.44 with ai 7.0.93: generateText tool call and
  result continuation, plus streamText passed. This is the documented OpenCode
  adapter family; it does not establish the exact bundled OpenCode version.
- OpenClaw cleanup plugin passes Node syntax check; lifecycle integration and
  remote deletion were not executed.
- Test environment reports a Starlette deprecation warning for httpx; tests
  still pass. No production dependency was added for the JavaScript checks.

## Repeat the tests

Install the Python test extra and run from the repository root. The test client
used by the supported Starlette release requires `httpx2`, which this extra
installs:

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -q
node --check openclaw-plugin/index.mjs
```

Start the isolated fixture server (never use it as a real provider):

```sh
.venv/bin/python -m uvicorn fixture_server:app --app-dir tests/integration --host 127.0.0.1 --port 18761
```

In another terminal, run tests/integration/openai_client.py with a Python
interpreter that has openai 2.24.0. Optional MIMICGATE_TEST_URL changes the
fixture URL; the historical MIMICGATE_TEST_URL remains a fallback. It defaults
to http://127.0.0.1:18761/v1.

For JavaScript, use a temporary directory under work, install the two pinned
packages there, and copy tests/integration/clients.mjs into that directory.
Set OPENCLAW_AI_DIR to the installed @openclaw/ai/dist directory, then run
`node clients.mjs`. Requires Node 22 or later. Stop the fixture server after
verification. The fixture returns synthetic answers by design; it cannot
verify a live website.

## Client setup

All clients use base URL http://127.0.0.1:11555/v1 (adjust for the actual server
port), model deepseek-chat or qwen-chat, and a non-secret dummy key if their
SDK requires one. The gateway itself currently has no bearer authentication.
The wire contract is client-brand neutral: the gateway does not select behavior
by detecting OpenClaw, Hermes or OpenCode in message text.
Tool definitions and tool results are forwarded as neutral model protocol data.
The agent remains responsible for tool execution and its own workflow policy.
Do not infer limits from Web product names: context and output limits are
unknown. Any manually configured client token budgets are operator estimates.
Use generous request timeouts for browser latency.

OpenCode custom provider:

```json
{
  "provider": {
    "mimicgate": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "MimicGate",
      "options": {"baseURL": "http://127.0.0.1:11555/v1", "apiKey": "local-mimicgate"},
      "models": {"deepseek-chat": {"name": "DeepSeek Web"}}
    }
  }
}
```

Select the historical `mimicgate/deepseek-chat` provider key. Hermes: run `hermes model`, choose a custom
OpenAI-compatible endpoint, enter the base URL, dummy key, model and an
operator-chosen context budget. OpenClaw: use api `openai-completions`, the
same base URL and a model entry; retain the optional session headers/cleanup
plugin when desired. The plugin is not required for model tool calling.

Sources checked for the intended client paths:
- [OpenCode providers](https://opencode.ai/docs/providers/): custom provider
  uses @ai-sdk/openai-compatible for Chat Completions.
- [Hermes quickstart](https://hermes-ai.net/docs/quickstart/): custom endpoint
  asks for base URL, key, model and context window.
- [OpenClaw model providers](https://docs.openclaw.ai/concepts/model-providers):
  custom OpenAI completions proxy configuration and timeout settings.

## Remaining live verification

Live environment check on 2026-09-05: the Qwen profile opened successfully
against `https://chat.qwen.ai/` with HTTP 200 and a visible authenticated chat
composer using the current selectors. This verified login/page readiness only;
no message was sent. The DeepSeek profile was locked by an already-running
Chrome process, so Playwright could not open it concurrently. DeepSeek live
completion remains unverified until that browser session is closed or exposed
through a supported attached-browser workflow.

Run each actual agent against each authenticated Web provider in an isolated
conversation: ask for one harmless tool, execute it in the agent, send its
result and verify the final answer. Include long replies, expired login,
changed DOM, cancellations and multiple sessions. Do not count the fixture
or historical project notes as evidence of current live success.
