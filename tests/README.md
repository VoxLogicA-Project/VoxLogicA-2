# VoxLogicA Test Infrastructure

This directory contains the complete test infrastructure for VoxLogicA-2, including test utilities, test runners, and organized test suites.

## Directory Structure

```
tests/
├── README.md                    # This file - test infrastructure documentation
├── run_tests.py                 # Main Python test runner
├── run-tests.sh                 # Shell script wrapper for test runner
├── voxlogica_testinfra.py       # Test infrastructure utilities
├── logs/                        # Test execution logs
├── chris_t1.nii.gz             # Test data (medical image)
└── [test_directories]/          # Individual test directories
    ├── __init__.py              # Makes directory a Python package
    ├── test_*.py                # Main test script
    └── [additional_files]       # Test-specific data or utilities
```

## Test Organization Principles

### Naming Conventions

- **Test directories**: Use descriptive names with underscores (e.g., `test_auto_cleanup`, `test_sha256_memoization`)
- **Test files**: Primary test file should be named `test_[feature].py` or match the directory name
- **Descriptions**: Each test must have a `description` variable explaining its purpose

### Directory Structure Pattern

Each test should be organized as follows:

```
test_[feature_name]/
├── __init__.py              # Empty file to make it a Python package
├── test_[feature_name].py   # Main test script
└── [optional_files]         # Test data, additional utilities, etc.
```

### Test File Structure Pattern

Every test file should follow this template:

```python
#!/usr/bin/env python3
"""
Brief description of what this test does.
"""

import sys
import os
from pathlib import Path
import argparse

# Standard path setup for VoxLogicA imports
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Import test infrastructure utilities if needed
from tests.voxlogica_testinfra import run_imgql_test

# MANDATORY: Description of the test
description = """Detailed description of what this test accomplishes, 
what features it exercises, and any special conditions or requirements."""

def main():
    """Main test function."""
    print(f"\nTest Description: {description}\n")
    
    parser = argparse.ArgumentParser(description="Description of test")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    # Add other test-specific arguments here
    
    args = parser.parse_args()
    
    # Test implementation here
    # Use test infrastructure utilities where appropriate
    # Return proper exit codes: 0 for success, 1 for failure
    

if __name__ == "__main__":
    main()
```

## Test Infrastructure Utilities

### voxlogica_testinfra.py

This module provides common utilities for testing VoxLogicA:

#### Functions

- `get_supported_languages()`: Returns list of supported implementation languages
- `get_voxlogica_cmd(language, imgql_path)`: Constructs command to run VoxLogicA with given language and file
- `run_imgql_test(imgql_path, language=None)`: Runs an imgql file and returns success/failure

#### Usage Example

```python
from tests.voxlogica_testinfra import run_imgql_test

# Run a test file with all supported languages
success = run_imgql_test("path/to/test.imgql")
if not success:
    sys.exit(1)

# Run with specific language only
success = run_imgql_test("path/to/test.imgql", language="python")
```

## Running Tests

### All Tests

```bash
# Using Python runner (recommended)
cd /path/to/VoxLogicA-2
python -m tests.run_tests

# Using shell wrapper
cd /path/to/VoxLogicA-2
./tests/run-tests.sh

# With specific language
python -m tests.run_tests --language python
```

### Individual Tests

```bash
# Run a specific test module
python -m tests.basic_test.test

# Run with language specification
python -m tests.basic_test.test --language python
```

### Test Logs

All test execution logs are stored in `tests/logs/` with filenames matching the test module name.

## Adding New Tests

### 1. Create Test Directory

```bash
mkdir tests/test_new_feature
touch tests/test_new_feature/__init__.py
```

### 2. Create Test File

Create `tests/test_new_feature/test_new_feature.py` following the standard pattern above.

### 3. Add to Test Runner

Edit `tests/run_tests.py` and add your test module to the `TEST_MODULES` list:

```python
TEST_MODULES = [
    # ... existing tests ...
    "tests.test_new_feature.test_new_feature",
]
```

### 4. Test Your Test

```bash
# Test individually first
python -m tests.test_new_feature.test_new_feature

# Then run full suite to ensure integration
python -m tests.run_tests
```

## Test Data

- Medical image test data is stored directly in the tests directory
- Test-specific data should be stored within the relevant test directory
- Large test files should be documented and their purpose explained

## Integration with Issues

When a test is created to address a specific issue:

1. **Issue Documentation**: The issue in `META/ISSUES/` must reference the test
2. **Test Documentation**: The test's description must reference the issue
3. **Cross-linking**: Both should contain clear references to each other
4. **Closure**: Issues should only be closed when their corresponding tests pass

Example test description for issue-related tests:

```python
description = """Test for issue META/ISSUES/OPEN/2025-06-20-auto-cleanup-feature:
Tests the ExecutionEngine auto-cleanup functionality that was implemented
to resolve stale operation accumulation problems."""
```

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure proper path setup in test files
2. **Missing Dependencies**: Check that test requirements are installed
3. **Virtual Environment**: Tests use the project's `.venv` - ensure it's properly set up
4. **Test Data**: Ensure required test data files are present

### Debugging Tests

1. Run individual tests first to isolate issues
2. Check test logs in `tests/logs/`
3. Use `--debug` flag if supported by the test
4. Verify virtual environment activation

## Best Practices

1. **Atomic Tests**: Each test should focus on a specific feature or scenario
2. **Clear Descriptions**: Always provide comprehensive test descriptions
3. **Error Handling**: Tests should handle errors gracefully and provide useful output
4. **Consistent Structure**: Follow the established patterns for maintainability
5. **Documentation**: Document any special requirements or test data needs
6. **Issue Linking**: Link tests to issues when applicable
7. **Cleanup**: Tests should clean up any temporary files or state they create
