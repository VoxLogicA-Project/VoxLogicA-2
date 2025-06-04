# VoxLogicA Features System

## Overview

The VoxLogicA features system provides a unified way to define functionality that can be accessed both through the command line interface (CLI) and the REST API. This ensures feature parity between the two interfaces and eliminates code duplication.

## Architecture

The features system is implemented in `implementation/python/voxlogica/features.py` and consists of:

1. **Feature Registry**: Central registry for all features
2. **Feature Definition**: Dataclass defining feature metadata and behavior
3. **Operation Result**: Standardized result wrapper for consistent error handling
4. **Handler Functions**: Implementation of the actual feature logic

## Core Components

### FeatureRegistry

The `FeatureRegistry` class manages all available features:

```python
class FeatureRegistry:
    _features: Dict[str, Feature] = {}

    @classmethod
    def register(cls, feature: Feature) -> Feature:
        """Register a new feature"""

    @classmethod
    def get_feature(cls, name: str) -> Feature:
        """Get a feature by name"""

    @classmethod
    def get_all_features(cls) -> Dict[str, Feature]:
        """Get all registered features"""
```

### Feature Definition

Each feature is defined using the `Feature` dataclass:

```python
@dataclass
class Feature:
    name: str                                    # Unique feature identifier
    description: str                             # Human-readable description
    handler: Callable                           # Function that implements the feature
    cli_options: Optional[Dict[str, Any]] = None # CLI-specific configuration
    api_endpoint: Optional[Dict[str, Any]] = None # API-specific configuration
```

### OperationResult

All feature handlers return an `OperationResult` for consistent error handling:

```python
class OperationResult(Generic[T]):
    def __init__(self, success: bool, data: Optional[T] = None, error: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error
```

## Adding a New Feature

To add a new feature that works in both CLI and API:

### 1. Create the Handler Function

```python
def handle_my_feature(
    required_param: str,
    optional_param: Optional[str] = None,
    **kwargs
) -> OperationResult[Dict[str, Any]]:
    """Handle my feature logic"""
    try:
        # Implement your feature logic here
        result_data = {"message": f"Processed {required_param}"}

        return OperationResult[Dict[str, Any]](
            success=True,
            data=result_data
        )
    except Exception as e:
        return OperationResult[Dict[str, Any]](
            success=False,
            error=str(e)
        )
```

### 2. Define CLI Configuration (Optional)

```python
cli_config = {
    "required_param": {
        "type": str,
        "required": True,
        "help": "Required parameter description"
    },
    "optional_param": {
        "type": str,
        "required": False,
        "help": "Optional parameter description"
    }
}
```

### 3. Define API Configuration (Optional)

```python
api_config = {
    "path": "/my-feature",
    "methods": ["POST"],
    "request_model": {
        "required_param": (str, "Required parameter description"),
        "optional_param": (Optional[str], "Optional parameter description")
    },
    "response_model": Dict[str, Any]
}
```

### 4. Register the Feature

```python
my_feature = FeatureRegistry.register(Feature(
    name="my_feature",
    description="Description of what my feature does",
    handler=handle_my_feature,
    cli_options=cli_config,
    api_endpoint=api_config
))
```

## Integration with CLI

The CLI automatically integrates registered features through:

1. **Command Detection**: The `run` command checks for feature-specific options
2. **Feature Execution**: `handle_cli_feature()` calls the appropriate handler
3. **Result Handling**: Success/error results are properly displayed to the user

Example CLI usage:

```bash
voxlogica run program.imgql --my-feature-option value
```

## Integration with API

The API automatically generates endpoints for registered features through:

1. **Endpoint Registration**: `register_api_endpoints()` creates FastAPI routes
2. **Request Validation**: Pydantic models are generated from feature definitions
3. **Response Handling**: Results are automatically converted to HTTP responses

Example API usage:

```bash
curl -X POST http://localhost:8000/api/v1/my-feature \
  -H "Content-Type: application/json" \
  -d '{"required_param": "value"}'
```

## Best Practices

### Handler Functions

- Always return `OperationResult` with appropriate typing
- Handle exceptions gracefully and return meaningful error messages
- Use \*\*kwargs to accept additional parameters for future extensibility
- Keep handlers focused on business logic, not interface concerns

### Error Handling

- Catch specific exceptions when possible
- Provide user-friendly error messages
- Use logging for debugging information
- Never let unhandled exceptions escape handlers

### Parameter Validation

- Define clear parameter types and requirements
- Use optional parameters with sensible defaults
- Document all parameters with helpful descriptions
- Validate inputs early in the handler

### Feature Naming

- Use descriptive, action-oriented names (e.g., "save_task_graph", "parse_program")
- Use snake_case for feature names
- Keep names concise but clear
- Avoid conflicting with existing CLI options

## Example: Complete Feature Implementation

Here's a complete example implementing a feature to validate programs:

```python
def handle_validate_program(
    program: str,
    filename: Optional[str] = None,
    strict: bool = False,
    **kwargs
) -> OperationResult[Dict[str, Any]]:
    """Validate a VoxLogicA program without executing it"""
    try:
        # Parse the program
        syntax = parse_program_string(program, filename or "unknown")

        # Perform validation
        errors = []
        warnings = []

        if strict:
            # Additional strict validation
            pass

        result = {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "syntax_tree_nodes": len(syntax.commands)
        }

        return OperationResult[Dict[str, Any]](
            success=True,
            data=result
        )

    except VLException as e:
        return OperationResult[Dict[str, Any]](
            success=False,
            error=f"Validation failed: {str(e)}"
        )

# Register the feature
validate_feature = FeatureRegistry.register(Feature(
    name="validate_program",
    description="Validate a VoxLogicA program without executing it",
    handler=handle_validate_program,
    cli_options={
        "strict": {
            "type": bool,
            "required": False,
            "default": False,
            "help": "Enable strict validation mode"
        }
    },
    api_endpoint={
        "path": "/validate",
        "methods": ["POST"],
        "request_model": {
            "program": (str, "The VoxLogicA program to validate"),
            "filename": (Optional[str], "Optional filename for error reporting"),
            "strict": (Optional[bool], "Enable strict validation mode")
        },
        "response_model": Dict[str, Any]
    }
))
```

This feature would then be available as:

- CLI: `voxlogica run program.imgql --validate --strict`
- API: `POST /api/v1/validate`
