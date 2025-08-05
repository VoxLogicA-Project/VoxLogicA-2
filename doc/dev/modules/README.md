# VoxLogicA-2 Python Modules Documentation

This directory contains detailed documentation for each Python implementation module in the VoxLogicA-2 codebase.

## Module Categories

### Core Logic Modules
- [**execution.py**](./execution.md) - Distributed execution engine with Dask integration
- [**reducer.py**](./reducer.md) - Core reduction engine and workplan management
- [**parser.py**](./parser.md) - VoxLogicA language parser and AST generation
- [**lazy.py**](./lazy.md) - Lazy compilation infrastructure

### Data Processing Modules  
- [**storage.py**](./storage.md) - Content-addressed storage and caching system
- [**features.py**](./features.md) - Feature system for extensible primitives
- [**converters/**](./converters.md) - Data format conversion utilities

### Interface Modules
- [**main.py**](./main.md) - Command line interface and entry points
- [**version.py**](./version.md) - Version management utilities

### Primitive Libraries
- [**primitives/**](./primitives.md) - Extensible primitive operation libraries
- [**stdlib/**](./stdlib.md) - Standard library implementations

## Architecture Overview

VoxLogicA-2 follows a modular architecture with clear separation of concerns:

1. **Language Layer**: `parser.py` handles VoxLogicA syntax and AST generation
2. **Compilation Layer**: `reducer.py` and `lazy.py` manage workplan compilation and optimization
3. **Execution Layer**: `execution.py` provides distributed execution via Dask
4. **Storage Layer**: `storage.py` handles content-addressed caching and persistence
5. **Extension Layer**: `features.py` and `primitives/` enable extensible operations

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
| `reducer.py` | Workplan compilation | AST reduction, environment management, goal processing |
| `parser.py` | Language parsing | VoxLogicA syntax, AST generation, expression parsing |
| `lazy.py` | Deferred compilation | Lazy evaluation, parameter binding, optimization |
| `storage.py` | Data persistence | Content-addressed storage, caching, serialization |
| `features.py` | Extensibility | Plugin system, primitive registration, dynamic loading |

## Navigation

Use the links above to navigate to specific module documentation. Each module page follows the same structure for consistency and ease of reference.
