# Task: Python Port of F# Implementation

## Summary

Port the current F# implementation (parser, reducer, main) to Python, using Lark for parsing. The Python version must:

- Replicate all features of the F# implementation.
- Be clear and modular, allowing for easy removal of unused features.
- Use Lark for parsing, starting with a grammar as close as possible to the F# version.
- Provide both CLI and HTTP API interfaces. The CLI and API must match exactly: every API endpoint must have a corresponding CLI switch, with data passed via files (CLI) or HTTP (API).
- Use standard, widely adopted, and stable tools for dependencies, linting, and testing, with a preference for Microsoft VSCode/Cursor compatibility.
- Include documentation (README, inline CLI docs, etc.) and maintain accuracy with respect to the implementation.
- Add a `tests/` directory at the top level, alongside `fsharp/` and the new `python/` directory, with at least one simple test.
- The design document for this port will be placed in the `doc/` directory, not in META.

## Issue

- GitHub Issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/1

## Status

- ✅ Implementation completed
- ✅ Python port created with full feature parity to F# implementation
- ✅ Created modular design with Lark parser, reducer, error handling, CLI, and API components
- ✅ Implemented FastAPI server and Typer CLI that match the F# CLI options
- ✅ Created test suite in `tests/` directory
- ✅ Added documentation (README, inline docs)
- ✅ Tested implementation and fixed issues with parser and reducer

## Implementation Details

- **Directory Structure:** Created `python/` directory at top level with Python implementation
- **Parser:** Used Lark for parsing, with grammar closely matching F# version
- **Reducer:** Implemented reducer logic for program evaluation
- **CLI:** Used Typer for CLI that matches F# CLI options
- **API:** Used FastAPI for API server with equivalent endpoints
- **Dependencies:** Used widely adopted tools (FastAPI, Typer, Lark)
- **Tests:** Added test suite in `tests/` directory and fixed issues during testing
- **Fixes:** Resolved issues with Lark transformer and made key classes hashable for the reducer

## Next Steps

1. Consider additional tests for more complex scenarios
2. Optimize performance if needed
3. Enhance documentation
