# features.py - Extensible Feature System

## Purpose

The `features.py` module implements a unified registry system for all VoxLogicA-2 features and operations. It provides a centralized mechanism for registering, discovering, and executing various functionality including CLI commands, API endpoints, and computational primitives.

## Architecture

### Core Components

#### 1. Feature Registry System
- **Centralized Registration**: Single source of truth for all features
- **Dynamic Discovery**: Features can be registered at runtime
- **Metadata Management**: Rich feature descriptions and configuration

#### 2. Operation Result Handling
- **Standardized Results**: Consistent success/error handling across operations
- **Type Safety**: Generic result types for compile-time type checking
- **Error Propagation**: Structured error information for debugging

#### 3. Feature Categories
- **CLI Features**: Command-line interface operations
- **API Features**: RESTful endpoint implementations
- **Computational Primitives**: Core VoxLogicA operations
- **Utility Features**: Helper functions and tools

### Key Classes and Interfaces

#### `Feature`
Base class representing a VoxLogicA feature.

```python
@dataclass
class Feature:
    name: str                           # Unique feature identifier
    description: str                    # Human-readable description
    handler: Callable                   # Function implementing the feature
    cli_options: Optional[Dict[str, Any]] = None    # CLI argument specifications
    api_endpoint: Optional[Dict[str, Any]] = None   # API endpoint configuration
```

#### `FeatureRegistry`
Central registry for feature management.

```python
class FeatureRegistry:
    @classmethod
    def register(cls, feature: Feature) -> Feature:
        """Register a new feature in the global registry."""
    
    @classmethod
    def get_feature(cls, name: str) -> Optional[Feature]:
        """Retrieve a feature by name."""
    
    @classmethod
    def get_all_features(cls) -> Dict[str, Feature]:
        """Get all registered features."""
```

#### `OperationResult`
Generic wrapper for operation results with error handling.

```python
class OperationResult(Generic[T]):
    def __init__(self, success: bool, data: Optional[T] = None, error: Optional[str] = None)
    
    success: bool           # Whether operation succeeded
    data: Optional[T]       # Result data if successful
    error: Optional[str]    # Error message if failed
```

## Implementation Details

### Feature Registration

Features are registered using a decorator pattern:

```python
def register_feature(
    name: str,
    description: str,
    cli_options: Optional[Dict[str, Any]] = None,
    api_endpoint: Optional[Dict[str, Any]] = None
) -> Callable:
    """Decorator for registering features."""
    
    def decorator(handler: Callable) -> Callable:
        feature = Feature(
            name=name,
            description=description,
            handler=handler,
            cli_options=cli_options,
            api_endpoint=api_endpoint
        )
        FeatureRegistry.register(feature)
        return handler
    
    return decorator
```

### Built-in Feature Handlers

#### Version Feature
```python
@register_feature(
    name="version",
    description="Display VoxLogicA-2 version information",
    cli_options={"aliases": ["-v", "--version"]}
)
def handle_version(**kwargs) -> OperationResult[Dict[str, str]]:
    """Handle version request."""
    from voxlogica.version import get_version
    
    return OperationResult[Dict[str, str]](
        success=True,
        data={"version": get_version()}
    )
```

#### Run Feature
```python
@register_feature(
    name="run",
    description="Execute VoxLogicA program",
    cli_options={
        "arguments": [
            {"name": "program", "type": str, "help": "Program source code"},
            {"name": "--filename", "type": str, "help": "Source file path"},
            {"name": "--execute", "action": "store_true", "help": "Execute the program"},
            {"name": "--debug", "action": "store_true", "help": "Enable debug mode"}
        ]
    }
)
def handle_run(
    program: str,
    filename: Optional[str] = None,
    execute: bool = True,
    debug: bool = False,
    **kwargs
) -> OperationResult[Dict[str, Any]]:
    """Execute VoxLogicA program with specified options."""
    
    try:
        # Parse program
        parsed_program = parse_program(program)
        
        # Compile to workplan
        workplan = reduce_program(parsed_program)
        
        # Execute if requested
        if execute:
            from voxlogica.execution import ExecutionEngine
            engine = ExecutionEngine()
            results = engine.execute_workplan(workplan)
            return OperationResult(success=True, data={"results": results})
        else:
            return OperationResult(success=True, data={"workplan": workplan})
            
    except Exception as e:
        return OperationResult(success=False, error=str(e))
```

### Dynamic Feature Loading

```python
def load_features_from_module(module_name: str) -> List[Feature]:
    """Dynamically load features from a Python module."""
    
    try:
        module = importlib.import_module(module_name)
        loaded_features = []
        
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, '_voxlogica_feature'):
                feature = attr._voxlogica_feature
                FeatureRegistry.register(feature)
                loaded_features.append(feature)
        
        return loaded_features
        
    except ImportError as e:
        logger.warning(f"Could not load features from {module_name}: {e}")
        return []
```

## Dependencies

### Internal Dependencies
- `voxlogica.parser` - Program parsing functionality
- `voxlogica.reducer` - Workplan compilation
- `voxlogica.execution` - Program execution
- `voxlogica.converters` - Data format conversion
- `voxlogica.version` - Version information

### External Dependencies
- `typing` - Type annotations and generics
- `dataclasses` - Feature data structures
- `tempfile` - Temporary file operations
- `json` - JSON serialization
- `logging` - Debug and error logging

## Usage Examples

### Registering Custom Features
```python
from voxlogica.features import FeatureRegistry, Feature, OperationResult

# Register a custom computational feature
def custom_operation(data: List[float]) -> OperationResult[float]:
    """Compute custom statistic on data."""
    try:
        result = sum(data) / len(data)  # Simple average
        return OperationResult(success=True, data=result)
    except Exception as e:
        return OperationResult(success=False, error=str(e))

# Create and register feature
custom_feature = Feature(
    name="average",
    description="Compute average of numeric data",
    handler=custom_operation,
    cli_options={
        "arguments": [
            {"name": "data", "type": list, "help": "List of numbers"}
        ]
    }
)

FeatureRegistry.register(custom_feature)
```

### Using Decorator Registration
```python
from voxlogica.features import register_feature, OperationResult

@register_feature(
    name="statistics",
    description="Compute data statistics",
    cli_options={
        "arguments": [
            {"name": "data", "help": "Input data"},
            {"name": "--mode", "choices": ["mean", "median", "std"], "default": "mean"}
        ]
    }
)
def compute_statistics(data: List[float], mode: str = "mean") -> OperationResult[float]:
    """Compute various statistics on numeric data."""
    
    try:
        if mode == "mean":
            result = sum(data) / len(data)
        elif mode == "median":
            sorted_data = sorted(data)
            n = len(sorted_data)
            result = sorted_data[n // 2] if n % 2 else (sorted_data[n//2-1] + sorted_data[n//2]) / 2
        elif mode == "std":
            mean = sum(data) / len(data)
            variance = sum((x - mean) ** 2 for x in data) / len(data)
            result = variance ** 0.5
        else:
            return OperationResult(success=False, error=f"Unknown mode: {mode}")
        
        return OperationResult(success=True, data=result)
        
    except Exception as e:
        return OperationResult(success=False, error=str(e))
```

### Feature Discovery and Execution
```python
from voxlogica.features import FeatureRegistry

# List all available features
all_features = FeatureRegistry.get_all_features()
for name, feature in all_features.items():
    print(f"{name}: {feature.description}")

# Execute a specific feature
feature = FeatureRegistry.get_feature("statistics")
if feature:
    result = feature.handler(data=[1, 2, 3, 4, 5], mode="mean")
    if result.success:
        print(f"Result: {result.data}")
    else:
        print(f"Error: {result.error}")
```

### CLI Integration
```python
def build_cli_parser():
    """Build argument parser from registered features."""
    
    parser = argparse.ArgumentParser(description="VoxLogicA-2 CLI")
    subparsers = parser.add_subparsers(dest="command")
    
    for name, feature in FeatureRegistry.get_all_features().items():
        if feature.cli_options:
            sub_parser = subparsers.add_parser(name, help=feature.description)
            
            for arg in feature.cli_options.get("arguments", []):
                sub_parser.add_argument(arg["name"], **{k: v for k, v in arg.items() if k != "name"})
    
    return parser
```

## Performance Considerations

### Feature Discovery
- **Lazy Loading**: Features are loaded only when needed
- **Caching**: Registry maintains cached feature lookup tables
- **Minimal Overhead**: Registration has negligible runtime cost

### Operation Execution
- **Type Safety**: Generic result types enable compile-time optimization
- **Error Handling**: Structured error handling reduces exception overhead
- **Resource Management**: Features can specify resource requirements

### Memory Management
- **Weak References**: Feature registry uses weak references where appropriate
- **Cleanup**: Automatic cleanup of temporary resources
- **Shared State**: Common resources are shared across feature instances

## Advanced Usage Patterns

### Feature Composition
```python
def compose_features(feature_names: List[str]) -> Feature:
    """Compose multiple features into a pipeline."""
    
    def composed_handler(**kwargs) -> OperationResult[Any]:
        current_data = kwargs
        
        for name in feature_names:
            feature = FeatureRegistry.get_feature(name)
            if not feature:
                return OperationResult(success=False, error=f"Feature not found: {name}")
            
            result = feature.handler(**current_data)
            if not result.success:
                return result
            
            current_data = {"data": result.data}
        
        return OperationResult(success=True, data=current_data["data"])
    
    return Feature(
        name="_".join(feature_names),
        description=f"Composed pipeline: {' -> '.join(feature_names)}",
        handler=composed_handler
    )
```

### Conditional Feature Loading
```python
def load_conditional_features():
    """Load features based on available dependencies."""
    
    # Only load GPU features if CUDA is available
    try:
        import cupy
        load_features_from_module("voxlogica.features.gpu")
        logger.info("GPU features loaded")
    except ImportError:
        logger.info("GPU features not available (CuPy not installed)")
    
    # Only load visualization features if matplotlib is available
    try:
        import matplotlib
        load_features_from_module("voxlogica.features.visualization")
        logger.info("Visualization features loaded")
    except ImportError:
        logger.info("Visualization features not available (matplotlib not installed)")
```

### Feature Validation
```python
def validate_feature(feature: Feature) -> List[str]:
    """Validate feature definition and return any issues."""
    
    issues = []
    
    if not feature.name:
        issues.append("Feature name cannot be empty")
    
    if not feature.description:
        issues.append("Feature description is required")
    
    if not callable(feature.handler):
        issues.append("Feature handler must be callable")
    
    # Validate CLI options format
    if feature.cli_options:
        if not isinstance(feature.cli_options, dict):
            issues.append("CLI options must be a dictionary")
    
    # Validate API endpoint format
    if feature.api_endpoint:
        required_keys = ["method", "path"]
        for key in required_keys:
            if key not in feature.api_endpoint:
                issues.append(f"API endpoint missing required key: {key}")
    
    return issues
```

## Integration Points

### With CLI System
Features are automatically integrated into the command-line interface:

```python
# In main.py
def main():
    parser = build_cli_from_features()
    args = parser.parse_args()
    
    feature = FeatureRegistry.get_feature(args.command)
    result = feature.handler(**vars(args))
    
    if result.success:
        print(json.dumps(result.data, indent=2))
    else:
        print(f"Error: {result.error}", file=sys.stderr)
        sys.exit(1)
```

### With API System
Features can be exposed as RESTful endpoints:

```python
# In api.py
def create_api_routes():
    app = Flask(__name__)
    
    for name, feature in FeatureRegistry.get_all_features().items():
        if feature.api_endpoint:
            endpoint = feature.api_endpoint
            
            @app.route(endpoint["path"], methods=[endpoint["method"]])
            def handle_request():
                result = feature.handler(**request.json)
                
                if result.success:
                    return jsonify(result.data)
                else:
                    return jsonify({"error": result.error}), 400
    
    return app
```

### With Execution Engine
Features can register computational primitives:

```python
# In execution.py
def register_computational_primitives():
    """Register feature handlers as computational primitives."""
    
    for name, feature in FeatureRegistry.get_all_features().items():
        if hasattr(feature, 'computational_primitive') and feature.computational_primitive:
            register_primitive(name, feature.handler)
```
