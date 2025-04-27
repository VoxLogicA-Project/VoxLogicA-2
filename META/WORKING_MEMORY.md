# Working Memory: Ongoing Activities

## 1. Python Port of F# Implementation

- **Task File:** See [META/TASK_python_port.md](TASK_python_port.md)
- **Status:** Implementation tested and fixed. All tests are passing.
- **Description:** Ported the F# implementation (parser, reducer, main) to Python, using Lark for parsing. Implemented all features, ensuring modularity, and provided both CLI and HTTP API interfaces using FastAPI. CLI and API match exactly. Implemented parser, reducer, error handling, CLI, and API server components.
- **Details:**
  - Created Python implementation in `python/` directory
  - Used Lark for parsing, closely matching F# grammar
  - Implemented reducer logic for evaluating VoxLogicA programs
  - Created CLI with Typer that matches F# CLI options
  - Implemented API server with FastAPI with equivalent endpoints
  - Added test suite in `tests/` directory
  - Created necessary documentation
  - Fixed parsing issues with Lark transformer
  - Made key classes hashable to support operations and goals
- **Next Steps:**
  - Refactor repo: move all implementation code to implementation/ (with python/ and fsharp/ as subdirs)
  - Move all test data (imgql files) to the global tests/ directory
  - Ensure the test script in tests/ runs both Python and F# tests
  - Remove test data from implementation directories
  - Consider additional tests for more complex scenarios
  - Optimize performance if needed
  - Enhance documentation
