# Tests

Pytest is the canonical test runner.

## Install Test Dependencies

From repo root:

```bash
pip install -r implementation/python/requirements-test.txt
```

or:

```bash
pip install -e implementation/python[test]
```

`requirements-test.txt` includes `hypothesis`.

## Run Tests

```bash
# full suite
./tests/run-tests.sh

# explicit pytest
pytest

# fast checks
pytest -m "unit or contract"

# integration + regression
pytest -m "integration or regression"

# perf marker
pytest -m perf --maxfail=1
```

## Notes

- `tests/run-tests.sh` installs test dependencies by default.
- Set `VOXLOGICA_SKIP_TEST_DEPS_INSTALL=1` to skip auto-install.
- Legacy script-style and superseded tests are preserved under `tests/archive/legacy/`.
