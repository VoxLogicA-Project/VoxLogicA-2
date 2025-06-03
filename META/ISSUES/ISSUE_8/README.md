# Issue 8: Tests not running and main executable not printing analysis

## Problem

After a previous change to unify API and CLI in a single source of truth:

1. The test runner script (`./tests/run-tests.sh`) was failing with error "Could not open requirements file: requirements-test.txt"
2. The main executable (`./voxlogica run test.imgql`) was running but not printing any analysis output

## Root Causes

1. **Missing requirements-test.txt**: The file `implementation/python/requirements-test.txt` was missing, causing the test runner to fail
2. **Silent success handling**: The `handle_cli_feature` function in `main.py` was only handling errors but not printing output when operations succeeded
3. **Incorrect test runner approach**: The bash test script was trying to run pytest instead of using the project's custom test infrastructure

## Resolution

### Fixed missing requirements-test.txt

- Created `implementation/python/requirements-test.txt` with:
  ```
  pytest>=7.0.0
  pytest-cov>=4.0.0
  pytest-mock>=3.10.0
  ```

### Fixed silent main executable

- Updated `handle_cli_feature` function in `main.py` to properly handle successful results
- Added detailed output for successful program analysis including:
  - Number of operations
  - Number of goals
  - Task graph output

### Fixed test runner

- Updated `tests/run-tests.sh` to use `python -m tests.run_tests` instead of pytest
- Fixed import issues in `tests/run_tests.py` to properly locate test modules
- Enhanced `tests/voxlogica_testinfra.py` to provide better output and error reporting

## Verification

Both test runners now work correctly:

- `python -m tests.run_tests --language python` - runs all integration tests
- `./tests/run-tests.sh --language python` - bash wrapper that runs the same tests

Main executable now provides detailed output:

```
$ ./voxlogica run test.imgql
2025-06-03 16:02:17,091 - voxlogica - INFO - VoxLogicA version: 0.1.0
2025-06-03 16:02:17,103 - voxlogica.cli - INFO - Successfully processed program:
2025-06-03 16:02:17,103 - voxlogica.cli - INFO -   Operations: 3
2025-06-03 16:02:17,103 - voxlogica.cli - INFO -   Goals: 1
2025-06-03 16:02:17,103 - voxlogica.cli - INFO -   Task graph:
goals: print(sum,2)
operations:
0 -> 1.0
1 -> 2.0
2 -> +(0,1)
```

## Status

**COMPLETED** - All tests are running successfully and main executable provides proper analysis output.

## Files Modified

- `implementation/python/requirements-test.txt` - Created missing file
- `implementation/python/voxlogica/main.py` - Enhanced CLI result handling
- `tests/run-tests.sh` - Fixed to use correct test infrastructure
- `tests/run_tests.py` - Fixed import paths
- `tests/voxlogica_testinfra.py` - Enhanced output and error reporting
