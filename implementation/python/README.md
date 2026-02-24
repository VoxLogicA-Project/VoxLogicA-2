# VoxLogicA 2 - Python Implementation

This is the Python implementation of VoxLogicA 2.

## Installation

### From Source

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e implementation/python
```

### Test Dependencies (includes Hypothesis)

Use either:

```bash
pip install -r implementation/python/requirements-test.txt
```

or:

```bash
pip install -e implementation/python[test]
```

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
pytest
```
