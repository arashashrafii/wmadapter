# Provider discovery and readiness

## Principle

Authentication state is the source of truth for runtime availability. A
provider is usable when its isolated browser session passes its readiness
probe. Static configuration describes known provider and model IDs; it does
not activate a provider.

## Runtime behavior

WMAdapter probes every configured provider at startup. A failed login for one
provider is recorded in `/ready` and does not prevent another ready provider
from serving requests.

`GET /ready` reports the overall gateway state and each provider's ready state,
including stable reason codes for login, challenge, startup, and probe failures.

`GET /v1/models` reports models of ready providers only. The endpoint is a
runtime availability contract, not a static configuration dump.

## Model routing

Requests with a model ID are resolved against all configured providers. The
provider readiness is checked immediately before inference. An unknown model
returns `404`; a known model whose provider is not ready returns `503` with
`provider_not_ready`.

The `providers.enabled`, `providers.enabled_models`, and `providers.default`
fields remain accepted for old YAML files, but are not required for discovery
or explicit model routing. A default is only a fallback for clients that omit
a model; OpenAI-compatible clients should send a model ID explicitly.

## OpenClaw workflow

1. Start WMAdapter with the intended config.
2. Authenticate the provider with `wmadapter ... login <provider>`.
3. Confirm readiness:

   ```bash
   wmadapter --config config.yaml check ready qwen
   ```

4. Confirm the live model catalog:

   ```bash
   curl http://127.0.0.1:11555/v1/models
   ```

5. Generate OpenClaw configuration using that same config.
6. Restart the OpenClaw gateway and select a model from the live catalog.

If a model appears in `openclaw.json` but not in `/v1/models`, the provider is
not ready or the two clients are using different environments.

## Product and test isolation

Product uses port `11555` and its configured profiles. Test uses port `11556`
and the profiles declared by `config.test.yaml`. Always pair the configuration
used to start WMAdapter with the configuration used to generate OpenClaw.

## Diagnostics versus discovery

The model endpoint should stay small and stable for clients. Use `/ready`,
`check`, and `doctor` for authentication, browser, and provider diagnostics.
