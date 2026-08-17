# Security Guidance

- Do not commit `config.yaml`, browser profiles, logs, credentials, or encryption keys.
- Prefer `python -m webbridgefreeride credentials set` over environment variables for repeat local use.
- The encrypted credential file is stored outside the repository by default under `~/.local/share/webbridgefreeride/`.
- The local encryption key is stored separately under `~/.config/webbridgefreeride/`.
- Logs redact common password, token, cookie, authorization, and credential values.
- This project automates a browser session. CAPTCHA, verification prompts, and provider policy changes can still require manual user action.
