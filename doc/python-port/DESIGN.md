# Design Document: Python Port of F# Implementation

## Overview

Port the F# implementation (parser, reducer, main) to Python, ensuring feature parity, modularity, and maintainability. The Python version will use Lark for parsing and provide both CLI and HTTP API interfaces.

## API Server Framework Choice

We have chosen **FastAPI** for the Python API server for the following reasons:

- **Type Hints:** FastAPI is built around Python type hints, enabling robust, self-documenting code and static analysis.
- **Automatic OpenAPI Docs:** FastAPI automatically generates OpenAPI (Swagger) documentation, making the API easy to explore, test, and share.
- **Cross-Language Compatibility:** The OpenAPI spec can be used to generate client/server code in other languages, supporting interoperability.
- **Modern Python Features:** FastAPI supports async programming, dependency injection, and leverages the latest Python features.
- **Community and Adoption:** FastAPI is widely adopted, well-documented, and actively maintained.
- **Conciseness:** FastAPI allows for concise, readable code, especially when combined with type hints and dataclasses.

## Coding Conventions

- Prefer functional programming style where possible.
- Use Python type hints extensively and leverage the latest Python version features.
- Use `dataclasses` for data structures when possible, instead of OOP patterns or classes with methods.
- Avoid object-oriented programming patterns; do not use inheritance or class-based design except where required by libraries (e.g., FastAPI models).

## Directory Structure

- The Python implementation resides in the `implementation/python/` directory.

## Testing

- The initial test will use a simple `imgql` file to verify basic parser and system functionality.
- Tests will be placed in a top-level `tests/` directory.

## CLI Design

- The initial CLI interface will be copied from the F# implementation to ensure feature parity and user familiarity.

## Python Version

- The target Python version for this project is **3.11+** to leverage modern language features and type hinting improvements while maintaining reasonable compatibility.

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

## Dependencies and Tooling

- Use pip for dependencies, unless a more widely adopted, stable, and industry-standard tool is identified that does not require a global environment.
- Set up linting and formatting for maximum compatibility with Microsoft VSCode/Cursor (e.g., black, ruff).

## Documentation

- Provide a README for the Python implementation.
- Include inline CLI documentation (e.g., --help flag).
- Maintain documentation accuracy with respect to the implementation.
