# VoxLogicA-2 Development Documentation

This directory contains comprehensive documentation for VoxLogicA-2 development, organized into modules and development notes.

## Module Documentation (`modules/`)

The `modules/` directory contains detailed documentation for each Python module in the VoxLogicA-2 implementation:

### Core System Modules

- **[README.md](modules/README.md)** - Overview of all modules with categorization and quick reference
- **[execution.md](modules/execution.md)** - Distributed execution engine with Dask integration
- **[reducer.md](modules/reducer.md)** - Core reduction engine and workplan compilation  
- **[parser.md](modules/parser.md)** - VoxLogicA language parser and AST generation
- **[lazy.md](modules/lazy.md)** - Lazy compilation infrastructure and deferred evaluation
- **[storage.md](modules/storage.md)** - Content-addressed storage system with SQLite backend

### Interface and Extension Modules

- **[features.md](modules/features.md)** - Extensible feature system and registry
- **[main.md](modules/main.md)** - CLI/API entry points and user interfaces
- **[converters.md](modules/converters.md)** - Data format conversion utilities
- **[primitives.md](modules/primitives.md)** - Extensible primitive operation libraries

Each module documentation includes:
- Architecture overview and design decisions
- Implementation details and key classes
- Usage examples and integration patterns  
- Performance considerations and optimization notes
- Dependencies and relationships with other modules

## Development Notes (`notes/`)

The `notes/` directory contains unified development documentation consolidating scattered information:

### System Architecture and Design

- **[development_overview.md](notes/development_overview.md)** - Comprehensive development guide covering:
  - Project architecture and design principles
  - Development workflow and best practices
  - Code quality standards and testing approach
  - Performance optimization strategies
  - Deployment and release processes

- **[closure_system.md](notes/closure_system.md)** - Environment management and closure execution:
  - Closure-based execution model
  - Environment creation and management
  - Variable binding and scoping
  - Evaluation strategies and optimization

- **[semantic_execution_engine.md](notes/semantic_execution_engine.md)** - Task scheduling and distributed execution:
  - Task queuing and dependency management
  - Lazy data structures and deferred computation
  - Dask integration and parallel execution
  - Memory management and optimization

- **[implementing_operators.md](notes/implementing_operators.md)** - Guide for extending VoxLogicA with new operators:
  - Primitive operation system architecture
  - Step-by-step implementation guide
  - Advanced features and optimization techniques
  - Testing and validation approaches

## Navigation Guide

### For New Developers

1. Start with [development_overview.md](notes/development_overview.md) for project understanding
2. Read [modules/README.md](modules/README.md) for system architecture overview
3. Study core modules: [execution.md](modules/execution.md), [reducer.md](modules/reducer.md), [parser.md](modules/parser.md)
4. Review [closure_system.md](notes/closure_system.md) for execution model understanding

### For Feature Development

1. Check [features.md](modules/features.md) for feature system architecture
2. Review [implementing_operators.md](notes/implementing_operators.md) for adding new operations
3. Study [primitives.md](modules/primitives.md) for primitive system details
4. Consult [semantic_execution_engine.md](notes/semantic_execution_engine.md) for execution integration

### For Performance Optimization

1. Review [execution.md](modules/execution.md) for distributed execution patterns
2. Study [lazy.md](modules/lazy.md) for lazy evaluation optimization
3. Check [storage.md](modules/storage.md) for storage performance considerations
4. Consult [semantic_execution_engine.md](notes/semantic_execution_engine.md) for memory management

### For Integration and Testing

1. Review [main.md](modules/main.md) for CLI/API integration patterns
2. Study [converters.md](modules/converters.md) for data format handling
3. Check [development_overview.md](notes/development_overview.md) for testing strategies
4. Consult module-specific documentation for integration examples

## Documentation Standards

All documentation follows consistent structure:

### Module Documentation Structure
- **Overview**: Purpose and role in the system
- **Architecture**: Design decisions and component relationships
- **Implementation**: Key classes, methods, and algorithms
- **Usage Examples**: Practical usage patterns and code samples
- **Performance**: Optimization notes and performance characteristics
- **Integration**: Dependencies and interaction with other modules

### Development Notes Structure
- **Overview**: Topic introduction and scope
- **Architecture**: System design and component organization
- **Implementation**: Detailed technical explanations and examples
- **Best Practices**: Recommended approaches and patterns
- **Advanced Topics**: Complex scenarios and optimization techniques

## Quick Reference

### Core Concepts
- **Spatial Model Checking**: Domain-specific language for declarative image analysis
- **Content-Addressed Storage**: Immutable data storage with SHA-256 based addressing
- **Lazy Evaluation**: Deferred computation for performance optimization
- **Distributed Execution**: Dask-based parallel processing for scalability
- **Feature Registry**: Plugin system for extensible CLI/API functionality

### Key Technologies
- **Python 3.8+**: Core implementation language
- **Dask**: Distributed computing framework
- **SQLite**: Content-addressed storage backend
- **Lark**: Parser generator for VoxLogicA language
- **SimpleITK**: Medical imaging operations library
- **Typer**: CLI framework
- **FastAPI**: REST API framework

### Development Workflow
1. **Setup**: Configure Python environment and install dependencies
2. **Development**: Implement features with comprehensive testing
3. **Testing**: Run unit tests, integration tests, and performance benchmarks
4. **Documentation**: Update relevant module and development documentation
5. **Integration**: Ensure proper integration with execution and storage systems

This documentation provides a comprehensive foundation for understanding, developing, and extending VoxLogicA-2. Each document is designed to be both a learning resource and a practical reference for ongoing development work.
