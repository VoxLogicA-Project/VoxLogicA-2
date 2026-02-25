# VoxLogicA 2 - Python Implementation

This is the Python implementation of VoxLogicA 2.

## Installation

### From Source

```bash
# install uv once (https://docs.astral.sh/uv/)

# from repo root; creates/updates .venv from .python-version + pinned requirements
python3 bootstrap.py --with-test
```

### Test Dependencies (includes Hypothesis)
Already included by `python3 bootstrap.py --with-test`.

## Usage

### CLI

```bash
# from repo root
./voxlogica version
./voxlogica run test.imgql
```

### API Server

```bash
./voxlogica serve
```

## Development

### Running Tests

```bash
# installs test dependencies first (unless VOXLOGICA_SKIP_TEST_DEPS_INSTALL=1)
./tests/run-tests.sh

# run only unit + contract
./tests/run-tests.sh -m "unit or contract"

# run full pytest directly
.venv/bin/python -m pytest
```

### Release Upgrade Helper

```bash
# validate pins, force-sync .venv, run full test suite
python3 implementation/python/release_upgrade.py
```

Interpreter pin:
- `.python-version` at repo root is the canonical Python version for bootstrap.
