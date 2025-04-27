# Design Document: Python Port of F# Implementation

## Overview

Port the F# implementation (parser, reducer, main) to Python, ensuring feature parity, modularity, and maintainability. The Python version will use Lark for parsing and provide both CLI and HTTP API interfaces.

## Parser

- Use Lark for the parser implementation.
- Initial grammar should closely match the F# version (imgql language).
- Parser should be modular to allow future evolution.

## Reducer

- Replicate the reducer logic from the F# implementation.
- Code should be clear and modular for easy maintenance and feature removal.

## CLI and API

- The CLI and API must match exactly: every API endpoint must have a corresponding CLI switch.
- CLI passes data via files; API uses HTTP (e.g., POST requests).
- The API server should use the most standard and simple Python web framework (to be finalized).
- The system can be run as a CLI tool or as an API server, controlled by a command-line switch.

## Testing

- Create a `tests/` directory at the top level, alongside `fsharp/` and `python/`.
- Add at least one simple test for the Python implementation.
- Use a standard Python testing framework (e.g., pytest).

## Dependencies and Tooling

- Use pip for dependencies, unless a more widely adopted, stable, and industry-standard tool is identified that does not require a global environment.
- Set up linting and formatting for maximum compatibility with Microsoft VSCode/Cursor (e.g., black, ruff).

## Documentation

- Provide a README for the Python implementation.
- Include inline CLI documentation (e.g., --help flag).
- Maintain documentation accuracy with respect to the implementation.
