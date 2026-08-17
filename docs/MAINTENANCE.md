# Maintenance Policy

Supported target:

- Local Linux, Python 3.11+
- DeepSeek Web through Playwright Chromium or a configured Chrome/Chromium executable

Operational checks:

- `python -m compileall src`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `/health` for process health
- `/ready` for browser/provider readiness

Known fragile areas:

- DeepSeek DOM selectors in `src/webbridgefreeride/providers/deepseek/selectors.py`
- Login challenges, CAPTCHA, and account verification
- Long-running browser profile state

Versioning:

- Milestone 5 is still a local-tool release level, not production SaaS hardening.
