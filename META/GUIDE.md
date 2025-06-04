# META Directory Guide

## Purpose

The `META` directory contains all records, policies, and documentation related to the software engineering (SWE) process, requirements, tasks, and issues for this project. It serves as the central location for process management and project organization artifacts.

## Usage

- **Record Keeping:** All requests, requirements, tasks, issues, and process-related discussions in chat must be formally recorded in the `META` directory.
- **Policies:** SWE policies and best practices are documented here (e.g., `SWE_POLICY.md`).
- **Guides:** This `GUIDE.md` provides an overview and must be kept up to date.
- **Emergent Structure:** The structure of `META` is flexible and should evolve as needed. Do not use rigid templates or boundaries.
- **Issues:** All issues are organized in `META/ISSUES/OPEN` (for open issues) and `META/ISSUES/CLOSED` (for closed issues), with directories named ISSUE_X where X is the GitHub issue number. Each issue directory contains a README.md file and any other relevant files for that issue.

## Current Working Memory

### Recent Completions

- **ISSUE_DAG_DICT_ARGS**: COMPLETED (commit 843ae10) - Successfully refactored DAG operations to use dict arguments with string numeric keys instead of lists. This improves extensibility while maintaining parser compatibility. All tests pass and documentation updated.

- **ISSUE_SHA256_IDS**: COMPLETED - Successfully implemented SHA256-based content-addressed IDs for all DAG nodes in the reducer module. Key achievements:
  - Replaced integer-based operation IDs with SHA256 hashes of canonical JSON representations
  - Used RFC 8785-compliant JSON canonicalization via `canonicaljson` library
  - Enhanced memoization with cross-session result reuse capabilities
  - Created comprehensive test suite demonstrating deterministic IDs, uniqueness, and memoization benefits
  - Maintained full backward compatibility with existing functionality
  - Foundation laid for persistent result caching and distributed execution

### Architecture Updates

- **DAG Structure**: Operations now use `Dict[str, OperationId]` arguments with keys "0", "1", "2", etc. for argument positions
- **Operation IDs**: Changed from integer-based to SHA256-based content-addressed IDs (`OperationId = str`)
- **Memoization**: Enhanced with content-addressed properties enabling cross-session result reuse
- **Test Coverage**:
  - Added comprehensive test suite for dict-based arguments in `tests/test_dag_dict_args.py`
  - Added SHA256 memoization tests in `tests/test_sha256_memoization.py` and `tests/test_sha256_memoization_advanced.py`
- **Documentation**: Updated reducer analysis and semantics documentation to reflect new argument structure and SHA256 ID scheme
- **Dependencies**: Added `canonicaljson>=2.0.0` for RFC 8785-compliant JSON canonicalization

## AI Responsibility

- The AI is responsible for keeping the `META` directory and this guide up to date, concise, and free of redundancy.
- The AI must ensure all relevant process changes, new policies, and important records are reflected here promptly.
