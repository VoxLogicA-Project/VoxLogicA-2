# VoxLogicA Tests

This directory contains tests for the VoxLogicA Python implementation.

## Running Tests

1. First, install the test dependencies:

```bash
pip install -r ../requirements-test.txt
```

2. Run the tests using the test runner script:

```bash
# Run all tests
python ../run_tests.py -v

# Run a specific test file
python ../run_tests.py tests/test_api.py -v

# Run tests with coverage report
python -m pytest --cov=voxlogica --cov-report=term-missing
```

## Test Structure

- `test_api.py`: Unit tests for the API endpoints using FastAPI's TestClient
- `test_integration.py`: Integration tests that start a real server and make HTTP requests
- `conftest.py`: Pytest fixtures and configuration

## Writing New Tests

When adding new tests:

1. For API endpoint tests, use the `api_client` fixture from `conftest.py`
2. For integration tests, use the `test_server_process` fixture
3. Add appropriate docstrings to document what each test is verifying
4. Follow the existing patterns for assertions and test organization

## Test Coverage

To generate a coverage report:

```bash
coverage run -m pytest
docker build -t voxlogica-tests .
coverage report -m
```

This will show you which parts of the codebase are not covered by tests.
