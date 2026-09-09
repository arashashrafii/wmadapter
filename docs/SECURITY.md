# Security Guidance

- Do not commit `config.yaml`, browser profiles, logs, credentials, or encryption keys.
- Never put chatbot passwords or tokens in `config.yaml`, commands, or logs.
- The encrypted credential file is stored outside the repository by default under
  the existing `~/.local/share/wmadapter/` compatibility path.
- The local encryption key is stored separately under the existing
  `~/.config/wmadapter/` compatibility path.
- Logs redact common password, token, cookie, authorization, and credential values.
- Both web adapters can require CAPTCHA or manual login. Tool markers are
  allowlisted and are never executed by the bridge itself.
