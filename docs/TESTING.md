# Testing

## Clean-install contract

Use a fresh Python environment for the repository test suite:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m compileall -q src
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
bash -n install.sh uninstall.sh tests/test_install.sh tests/test_uninstall.sh
bash tests/test_install.sh
bash tests/test_uninstall.sh
```

The `test` extra is required because the HTTP contract tests use Starlette's
test client through `httpx2`. Installing the base package alone is sufficient
for runtime use but is not a complete test environment. The same contract is
enforced by `.github/workflows/python-tests.yml` on Python 3.11 and 3.12.

## Node/Playwright tests

From a clean clone, install the pinned Node dependency graph from the lockfile:

```bash
npm ci
npx playwright install --with-deps
npm test
```

`npm ci` is required for reproducible CI installs. The browser-install step is
required before the first Playwright run; `--with-deps` also installs Linux
browser libraries in CI. The current Playwright fixture is a browser smoke
fixture and is not provider authentication or live MimicGate verification.
