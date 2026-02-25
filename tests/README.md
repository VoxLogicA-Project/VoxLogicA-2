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
- Canonical static test assets live under `tests/data/` (for example `tests/data/chris_t1.nii.gz`).
- Test dashboards consume artifacts written under `tests/reports/`:
  - `junit.xml`
  - `coverage.xml`
  - `perf/vox1_vs_vox2_perf.json`
  - `perf/vox1_vs_vox2_perf.svg`
