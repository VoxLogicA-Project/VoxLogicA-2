# Tests

Pytest is the canonical test runner.

## Install Test Dependencies

From repo root:

```bash
# install uv once (https://docs.astral.sh/uv/)

python3 bootstrap.py --with-test
```

`requirements-test.txt` includes `hypothesis`.

## Run Tests

```bash
# full suite
./tests/run-tests.sh

# explicit pytest
.venv/bin/python -m pytest

# fast checks
.venv/bin/python -m pytest -m "unit or contract"

# integration + regression
.venv/bin/python -m pytest -m "integration or regression"

# perf marker
.venv/bin/python -m pytest -m perf --maxfail=1
```

## Release Upgrade Helper

```bash
# validate pins, force-sync .venv, then run tests
python3 implementation/python/release_upgrade.py
```

## Notes

- `tests/run-tests.sh` bootstraps `.venv` from pinned requirements by default.
- Set `VOXLOGICA_SKIP_TEST_DEPS_INSTALL=1` to skip auto-install.
- VoxLogicA-1 parity/perf tests auto-fetch the pinned release (`v1.3.3-experimental`) into a repo-local cache at `.cache/vox1/` unless `VOXLOGICA1_EXPERIMENTAL_BIN` is set.
- Canonical static test assets live under `tests/data/` (for example `tests/data/chris_t1.nii.gz`).
- Test dashboards consume artifacts written under `tests/reports/`:
  - `junit.xml`
  - `coverage.xml`
  - `perf/vox1_vs_vox2_perf.json`
  - `perf/vox1_vs_vox2_perf.svg`
