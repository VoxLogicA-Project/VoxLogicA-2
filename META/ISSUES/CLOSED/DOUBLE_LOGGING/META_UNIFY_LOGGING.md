# [META] Unify Logging: Remove Custom Logger Class and Use Standard Python Logging

## Background

Currently, the codebase suffers from double logging due to the coexistence of a custom Logger class (`error_msg.py`) and standard Python logging configuration (`main.py`). This results in duplicate log messages and inconsistent logging practices.

## Goal

- Remove the custom Logger class from the codebase.
- Standardize all logging to use the standard Python logging module.
- Ensure a single, unified logging format and configuration, set once at application startup.
- Update all modules to use `logging.getLogger()` and standard logging calls.

## Steps

1. Remove the custom Logger class from `error_msg.py`.
2. Update all code that uses `Logger.info()`, `Logger.error()`, etc., to use a module-level logger:
   ```python
   import logging
   logger = logging.getLogger("voxlogica.<module>")  # or use __name__
   ```
3. Ensure `main.py` (or the application entry point) configures logging once using `logging.basicConfig()` or similar.
4. Remove any per-module handler additions or custom logger instantiations.
5. Test to confirm that:
   - Only one log message appears per event.
   - The log format is consistent across all modules.
   - Log levels and handlers work as expected.

## Acceptance Criteria

- No duplicate log messages in any mode (CLI, API, etc.).
- All log output uses the unified format.
- No references to the custom Logger class remain in the codebase.
- Logging configuration is centralized and only set once.
