# VoxLogicA-2 Python Modules Documentation

This directory contains detailed documentation for each Python implementation module in the VoxLogicA-2 codebase.

## Module Categories

### Core Logic Modules
- [**execution.py**](./execution.md) - Runtime facade over pluggable execution strategies
- [**execution_strategy/**](./execution.md) - Strategy contracts and strict/dask implementations
- [**reducer.py**](./reducer.md) - Symbolic reduction engine and workplan management
- [**parser.py**](./parser.md) - VoxLogicA language parser and AST generation
- [**lazy/**](./lazy.md) - Symbolic IR and plan hashing infrastructure

### Data Processing Modules  
- [**storage.py**](./storage.md) - Modular results database + runtime stores
- [**features.py**](./features.md) - Feature system for extensible primitives
- [**converters/**](./converters.md) - Data format conversion utilities

### Interface Modules
- [**main.py**](./main.md) - Command line interface and entry points
- [**repl.py**](./repl.md) - Interactive session runtime for CLI/GUI embedding
- [**version.py**](./version.md) - Version management utilities

### Primitive Libraries
- [**primitives/**](./primitives.md) - Extensible primitive operation libraries
- [**stdlib/**](./stdlib.md) - Standard library implementations

## Architecture Overview

VoxLogicA-2 follows a modular architecture with clear separation of concerns:

1. **Language Layer**: `parser.py` handles VoxLogicA syntax and AST generation
2. **Compilation Layer**: `reducer.py` and `lazy/` manage symbolic plan construction
3. **Execution Layer**: `execution.py` + `execution_strategy/` provide pluggable runtimes
4. **Storage Layer**: `storage.py` defines definition/materialization stores and modular result DB backends
5. **Extension Layer**: `features.py`, `repl.py`, and `primitives/` expose CLI/API/interactive workflows

## Documentation Standards

Each module documentation includes:
- **Purpose**: What the module does and why it exists
- **Architecture**: How the module is structured and its key components
- **Key Classes/Functions**: Main components and their responsibilities
- **Dependencies**: What other modules this depends on
- **Usage Examples**: How to use the module
- **Implementation Notes**: Important implementation details
- **Performance Considerations**: Scalability and optimization notes

## Quick Reference

| Module | Primary Purpose | Key Features |
|--------|----------------|--------------|
| `execution.py` | Distributed execution | Dask integration, futures coordination, task scheduling |
| `reducer.py` | Workplan compilation | AST reduction, symbolic plan generation, goal processing |
| `parser.py` | Language parsing | VoxLogicA syntax, AST generation, expression parsing |
| `lazy/` | Symbolic IR | Node definitions, canonical hashing, plan contracts |
| `storage.py` | Data persistence | Results database API, materialization tracking |
| `features.py` | Extensibility | Plugin system, primitive registration, dynamic loading |
| `repl.py` | Interactive execution | Incremental context, expression evaluation, result persistence |

## Navigation

Use the links above to navigate to specific module documentation. Each module page follows the same structure for consistency and ease of reference.
