# Development Overview

## Project Architecture

VoxLogicA-2 is organized as a modular, distributed system for spatial model checking and declarative image analysis. The architecture follows functional programming principles with immutable data structures and lazy evaluation.

### Core Principles

1. **Immutability**: Data structures are immutable to enable safe parallel execution
2. **Content-Addressed Storage**: Results are cached using content hashes for deduplication
3. **Lazy Evaluation**: Computations are deferred until results are actually needed
4. **Distributed Execution**: Workplans are executed using Dask for scalability
5. **Extensibility**: Plugin-based architecture for adding new operations and features

### System Layers

```
┌─────────────────────────────────────────────┐
│              User Interfaces               │
│          (CLI, API, Web Dashboard)          │
├─────────────────────────────────────────────┤
│               Features System               │
│         (Unified Feature Registry)          │
├─────────────────────────────────────────────┤
│              Language Layer                 │
│         (Parser, AST, Expressions)          │
├─────────────────────────────────────────────┤
│             Compilation Layer               │
│        (Reducer, Lazy Compilation)          │
├─────────────────────────────────────────────┤
│             Execution Layer                 │
│     (Distributed Execution, Task Queue)     │
├─────────────────────────────────────────────┤
│              Storage Layer                  │
│    (Content-Addressed Cache, Persistence)   │
├─────────────────────────────────────────────┤
│             Primitives Layer                │
│      (Extensible Operation Libraries)       │
└─────────────────────────────────────────────┘
```

## Development Workflow

### Setting Up Development Environment

1. **Clone Repository**:
   ```bash
   git clone https://github.com/voxlogica-project/VoxLogicA-2.git
   cd VoxLogicA-2
   ```

2. **Install Dependencies**:
   ```bash
   cd implementation/python
   pip install -r requirements.txt
   pip install -r requirements-test.txt
   ```

3. **Run Tests**:
   ```bash
   cd tests
   python run_tests.py
   # Or use the shell script
   ./run-tests.sh
   ```

### Code Organization

The Python implementation follows this structure:

```
implementation/python/voxlogica/
├── __init__.py              # Package initialization
├── main.py                  # CLI and API entry points
├── parser.py                # VoxLogicA language parser
├── reducer.py               # Workplan compilation and reduction
├── execution.py             # Distributed execution engine
├── lazy.py                  # Lazy compilation infrastructure
├── storage.py               # Content-addressed storage
├── features.py              # Feature registry system
├── version.py               # Version management
├── converters/              # Data format converters
│   ├── json_converter.py    # JSON serialization
│   └── dot_converter.py     # Graph visualization
├── primitives/              # Operation libraries
│   ├── default/             # Built-in operations
│   ├── simpleitk/           # Medical imaging operations
│   └── test/                # Testing operations
└── stdlib/                  # Standard library
```

### Testing Strategy

VoxLogicA-2 uses a comprehensive testing approach:

1. **Unit Tests**: Test individual functions and classes
2. **Integration Tests**: Test module interactions
3. **End-to-End Tests**: Test complete workflows
4. **Performance Tests**: Validate scalability and resource usage
5. **Regression Tests**: Prevent feature regressions

Test files are organized in the `tests/` directory:

```
tests/
├── run_tests.py             # Main test runner
├── run-tests.sh             # Shell script runner
├── voxlogica_testinfra.py   # Test infrastructure
├── basic_test/              # Basic functionality tests
├── features/                # Feature-specific tests
└── test_*/                  # Specific test categories
```

### Adding New Features

1. **Create Feature Handler**:
   ```python
   def handle_new_feature(**kwargs) -> OperationResult[Any]:
       """Handle new feature implementation."""
       try:
           # Implement feature logic
           result = process_feature_request(**kwargs)
           return OperationResult(success=True, data=result)
       except Exception as e:
           return OperationResult(success=False, error=str(e))
   ```

2. **Register Feature**:
   ```python
   new_feature = Feature(
       name="new_feature",
       description="Description of the new feature",
       handler=handle_new_feature,
       cli_options={
           "arguments": [
               {"name": "input", "help": "Input parameter"}
           ]
       }
   )
   FeatureRegistry.register(new_feature)
   ```

3. **Add Tests**:
   ```python
   def test_new_feature():
       feature = FeatureRegistry.get_feature("new_feature")
       result = feature.handler(input="test_data")
       assert result.success
       assert result.data is not None
   ```

### Adding New Primitives

1. **Create Primitive Class**:
   ```python
   class NewOperation(PrimitiveOperation):
       def __init__(self):
           super().__init__("new_op", "Description of new operation")
           self.input_types = [str, int]
           self.output_type = str
       
       def execute(self, text: str, count: int) -> str:
           return text * count
   ```

2. **Register in Appropriate Library**:
   ```python
   # In primitives/default/__init__.py
   from .new_operation import NewOperation
   
   # Registration happens automatically via discovery
   ```

3. **Add Comprehensive Tests**:
   ```python
   def test_new_operation():
       op = NewOperation()
       result = op.execute("hello", 3)
       assert result == "hellohellohello"
   ```

## Code Quality Standards

### Python Style Guide

- **PEP 8 Compliance**: Follow Python PEP 8 style guidelines
- **Type Hints**: Use type hints for all function signatures
- **Docstrings**: Provide comprehensive docstrings for all public APIs
- **Modern Syntax**: Use Python 3.11+ features and syntax

Example:
```python
def process_workplan(
    workplan: WorkPlan,
    options: Dict[str, Any] = None
) -> ExecutionResult:
    """
    Process a VoxLogicA workplan with specified options.
    
    Args:
        workplan: The workplan to execute
        options: Optional execution parameters
        
    Returns:
        Execution result with success status and data
        
    Raises:
        ExecutionError: If workplan execution fails
    """
    if options is None:
        options = {}
    
    # Implementation here
    pass
```

### Error Handling

- **Structured Exceptions**: Use specific exception types
- **Error Context**: Provide meaningful error messages with context
- **Graceful Degradation**: Handle errors gracefully when possible
- **Logging**: Log errors with appropriate levels

```python
class VoxLogicAError(Exception):
    """Base exception for VoxLogicA errors."""
    pass

class CompilationError(VoxLogicAError):
    """Error during workplan compilation."""
    pass

class ExecutionError(VoxLogicAError):
    """Error during workplan execution."""
    pass
```

### Logging Standards

```python
import logging

logger = logging.getLogger(__name__)

def complex_operation():
    logger.debug("Starting complex operation")
    try:
        # Operation implementation
        logger.info("Complex operation completed successfully")
    except Exception as e:
        logger.error(f"Complex operation failed: {e}", exc_info=True)
        raise
```

## Performance Guidelines

### Memory Management

- **Lazy Loading**: Load data only when needed
- **Weak References**: Use weak references for caches when appropriate
- **Resource Cleanup**: Ensure proper cleanup of resources
- **Memory Profiling**: Profile memory usage during development

### Concurrency

- **Thread Safety**: Ensure thread-safe operations for shared data
- **Lock-Free Design**: Prefer lock-free algorithms when possible
- **Async/Await**: Use async/await for I/O operations
- **Resource Limits**: Implement resource limits to prevent exhaustion

### Caching Strategy

- **Content Addressing**: Use content hashes for cache keys
- **Cache Invalidation**: Implement proper cache invalidation
- **Cache Levels**: Multiple cache levels (memory, disk, distributed)
- **Cache Monitoring**: Monitor cache performance and hit rates

## Security Considerations

### Input Validation

- **Sanitize Inputs**: Validate and sanitize all user inputs
- **Type Checking**: Use type checking to prevent type confusion
- **Bounds Checking**: Validate array bounds and numeric ranges
- **Path Traversal**: Prevent directory traversal attacks

### Resource Protection

- **Resource Limits**: Implement limits on memory, CPU, and disk usage
- **Timeout Management**: Set appropriate timeouts for operations
- **Privilege Separation**: Run with minimal required privileges
- **Audit Logging**: Log security-relevant events

## Documentation Standards

### Code Documentation

- **Module Docstrings**: Document module purpose and usage
- **Class Docstrings**: Explain class purpose and key methods
- **Function Docstrings**: Document parameters, returns, and exceptions
- **Inline Comments**: Explain complex logic and algorithms

### API Documentation

- **OpenAPI Specs**: Maintain OpenAPI specifications for REST APIs
- **Examples**: Provide working examples for all APIs
- **Error Codes**: Document all possible error conditions
- **Versioning**: Document API versions and compatibility

### User Documentation

- **Installation Guides**: Clear installation instructions
- **Usage Examples**: Practical examples for common tasks
- **Troubleshooting**: Common issues and solutions
- **Migration Guides**: Version upgrade instructions

## Version Control Workflow

### Branch Strategy

- **main**: Stable release branch
- **develop**: Integration branch for new features
- **feature/***: Feature development branches
- **hotfix/***: Critical bug fix branches
- **release/***: Release preparation branches

### Commit Standards

- **Conventional Commits**: Use conventional commit message format
- **Atomic Commits**: Each commit should represent a single logical change
- **Descriptive Messages**: Clear, descriptive commit messages
- **Issue Linking**: Link commits to relevant issues

Example commit message:
```
feat(execution): add distributed task scheduling

- Implement Dask-based task scheduling
- Add resource management for worker pools
- Include monitoring and debugging features

Closes #123
```

### Code Review Process

1. **Create Feature Branch**: Branch from develop for new work
2. **Implement Changes**: Follow coding standards and add tests
3. **Self Review**: Review your own changes before submission
4. **Create Pull Request**: Submit PR with clear description
5. **Peer Review**: At least one reviewer required
6. **Address Feedback**: Respond to reviewer comments
7. **Merge**: Merge after approval and CI passes

## Continuous Integration

### Automated Testing

- **Unit Tests**: Run on every commit
- **Integration Tests**: Run on pull requests
- **Performance Tests**: Run on release candidates
- **Security Scans**: Regular security vulnerability scans

### Quality Gates

- **Code Coverage**: Maintain minimum code coverage thresholds
- **Style Checking**: Automated style and lint checking
- **Type Checking**: Static type checking with mypy
- **Documentation**: Ensure documentation is updated

### Deployment Pipeline

1. **Development**: Continuous testing and integration
2. **Staging**: Integration testing in production-like environment
3. **Production**: Automated deployment with rollback capability
4. **Monitoring**: Continuous monitoring and alerting

## Debugging and Troubleshooting

### Debugging Tools

- **Python Debugger**: Use pdb/ipdb for interactive debugging
- **Logging**: Comprehensive logging at appropriate levels
- **Profiling**: Use cProfile and memory_profiler for performance analysis
- **Dask Dashboard**: Monitor distributed execution

### Common Issues

1. **Memory Leaks**: Monitor memory usage and clean up resources
2. **Deadlocks**: Avoid locks or use timeout-based locking
3. **Performance**: Profile bottlenecks and optimize critical paths
4. **Concurrency**: Test thoroughly for race conditions

### Monitoring

- **Application Metrics**: Monitor key application metrics
- **System Metrics**: Monitor CPU, memory, and disk usage
- **Error Tracking**: Centralized error collection and analysis
- **Performance Monitoring**: Track response times and throughput
