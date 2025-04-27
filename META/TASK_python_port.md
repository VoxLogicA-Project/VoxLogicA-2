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

- Design documents skeleton to be created in `doc/`.
- API/server framework discussion pending.
- Awaiting design approval before implementation.

## Next Steps

1. Create design documents skeleton in `doc/`.
2. Discuss and finalize API/server framework.
3. Proceed to implementation after design approval.
