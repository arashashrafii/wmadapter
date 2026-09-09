# Security Guidance

## Provider account safety

Web providers may suspend accounts after high-volume browser automation,
repeated retries, CAPTCHA interaction, or policy-sensitive test prompts. Never
run the full live compatibility suite against a personal or production
account. Use a dedicated test account, keep live runs bounded, and run
contract and negative cases against the local fixture transport.

Web Model Adapter detects provider suspension pages as a terminal
`ACCOUNT_SUSPENDED` state and disables automatic login retries. Re-authenticate
manually or use a separate test account before attempting another live run.

Uninstall removes the persistent Web Model Adapter provider profile directory
under `~/.local/share/wmadapter/profiles`, so reinstalling starts with a fresh
browser profile and requires a new provider login. Credentials outside that
profile are not removed automatically.

- Do not commit `config.yaml`, browser profiles, logs, credentials, or encryption keys.
- Never put chatbot passwords or tokens in `config.yaml`, commands, or logs.
- Provider usernames and passwords are not accepted, logged, or stored. Login is
  performed manually in the isolated Chromium profile; only browser-managed
  session state remains in that profile.
- Logs redact common password, token, cookie, authorization, and credential values.
- Both web adapters can require CAPTCHA or manual login. Tool markers are
  allowlisted and are never executed by the bridge itself.
