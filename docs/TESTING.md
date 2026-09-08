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
