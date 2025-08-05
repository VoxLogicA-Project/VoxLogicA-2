# converters/ - Data Format Conversion Utilities

## Purpose

The `converters/` package provides utilities for converting VoxLogicA-2 data structures to various formats for visualization, serialization, and interoperability. It includes converters for JSON, DOT graphs, and other output formats.

## Architecture

### Core Components

#### 1. JSON Converter (`json_converter.py`)
- **WorkPlan Serialization**: Convert workplans to JSON for storage and transmission
- **Custom Encoders**: Handle VoxLogicA-specific data types
- **Pretty Printing**: Human-readable JSON output with proper formatting

#### 2. DOT Graph Converter (`dot_converter.py`) 
- **Graph Visualization**: Convert workplans to DOT format for Graphviz
- **Dependency Visualization**: Show operation dependencies as directed graphs
- **Layout Options**: Various graph layouts and styling options

#### 3. Format Registry
- **Pluggable Converters**: Extensible system for adding new output formats
- **Auto-Detection**: Automatic format detection based on file extensions
- **Validation**: Format validation and error handling

### Key Classes and Functions

#### JSON Converter
```python
class WorkPlanJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for VoxLogicA workplans and data structures."""
    
    def default(self, obj: Any) -> Any:
        """Convert VoxLogicA objects to JSON-serializable format."""
        
        if isinstance(obj, WorkPlan):
            return {
                'type': 'WorkPlan',
                'operations': obj.operations,
                'goals': obj.goals,
                'dependencies': obj.dependencies
            }
        elif isinstance(obj, Operation):
            return {
                'type': 'Operation',
                'primitive_name': obj.primitive_name,
                'arguments': obj.arguments,
                'node_id': obj.node_id,
                'content_hash': obj.content_hash
            }
        # Handle other VoxLogicA types...
        
        return super().default(obj)

def workplan_to_json(workplan: WorkPlan, pretty: bool = True) -> str:
    """Convert workplan to JSON string."""
    
    if pretty:
        return json.dumps(workplan, cls=WorkPlanJSONEncoder, indent=2)
    else:
        return json.dumps(workplan, cls=WorkPlanJSONEncoder)

def json_to_workplan(json_str: str) -> WorkPlan:
    """Convert JSON string back to workplan."""
    
    data = json.loads(json_str)
    return deserialize_workplan(data)
```

#### DOT Graph Converter
```python
def workplan_to_dot(
    workplan: WorkPlan,
    include_goals: bool = True,
    include_hashes: bool = False,
    layout: str = "TB"  # Top-Bottom, Left-Right, etc.
) -> str:
    """Convert workplan to DOT graph format."""
    
    lines = [
        f'digraph WorkPlan {{',
        f'  rankdir={layout};',
        f'  node [shape=box, style=rounded];'
    ]
    
    # Add operation nodes
    for node_id, operation in workplan.operations.items():
        label = f"{operation.primitive_name}"
        if include_hashes:
            label += f"\\n{operation.content_hash[:8]}..."
        
        lines.append(f'  "{node_id}" [label="{label}"];')
    
    # Add dependency edges
    for node_id, deps in workplan.dependencies.items():
        for dep in deps:
            lines.append(f'  "{dep}" -> "{node_id}";')
    
    # Add goal nodes if requested
    if include_goals:
        for goal_name, goal in workplan.goals.items():
            lines.append(f'  "{goal.node_id}" [color=red, label="{goal_name}"];')
    
    lines.append('}')
    return '\n'.join(lines)

def save_dot_file(workplan: WorkPlan, filename: str, **kwargs) -> None:
    """Save workplan as DOT file."""
    
    dot_content = workplan_to_dot(workplan, **kwargs)
    
    with open(filename, 'w') as f:
        f.write(dot_content)
```

#### Format Registry
```python
class ConverterRegistry:
    """Registry for data format converters."""
    
    _converters: Dict[str, Dict[str, Callable]] = {
        'json': {
            'encode': workplan_to_json,
            'decode': json_to_workplan,
            'extensions': ['.json']
        },
        'dot': {
            'encode': workplan_to_dot,
            'decode': None,  # DOT is output-only
            'extensions': ['.dot', '.gv']
        }
    }
    
    @classmethod
    def register_converter(
        cls,
        format_name: str,
        encoder: Callable,
        decoder: Optional[Callable] = None,
        extensions: List[str] = None
    ) -> None:
        """Register a new format converter."""
        
        cls._converters[format_name] = {
            'encode': encoder,
            'decode': decoder,
            'extensions': extensions or []
        }
    
    @classmethod
    def get_converter(cls, format_name: str) -> Optional[Dict[str, Callable]]:
        """Get converter for specified format."""
        return cls._converters.get(format_name)
    
    @classmethod
    def detect_format(cls, filename: str) -> Optional[str]:
        """Detect format from file extension."""
        
        ext = Path(filename).suffix.lower()
        
        for format_name, info in cls._converters.items():
            if ext in info.get('extensions', []):
                return format_name
        
        return None
```

## Implementation Details

### JSON Serialization Strategy

The JSON converter handles various VoxLogicA data types:

```python
def serialize_voxlogica_object(obj: Any) -> Dict[str, Any]:
    """Serialize VoxLogicA objects to JSON-compatible dictionaries."""
    
    if isinstance(obj, WorkPlan):
        return {
            'type': 'WorkPlan',
            'version': '2.0',
            'operations': {k: serialize_voxlogica_object(v) for k, v in obj.operations.items()},
            'goals': {k: serialize_voxlogica_object(v) for k, v in obj.goals.items()},
            'dependencies': {k: list(v) for k, v in obj.dependencies.items()},
            'metadata': getattr(obj, 'metadata', {})
        }
    
    elif isinstance(obj, Operation):
        return {
            'type': 'Operation',
            'primitive_name': obj.primitive_name,
            'arguments': obj.arguments,
            'node_id': obj.node_id,
            'content_hash': obj.content_hash,
            'metadata': getattr(obj, 'metadata', {})
        }
    
    elif isinstance(obj, Goal):
        return {
            'type': 'Goal',
            'node_id': obj.node_id,
            'name': getattr(obj, 'name', ''),
            'description': getattr(obj, 'description', '')
        }
    
    elif isinstance(obj, Environment):
        return {
            'type': 'Environment',
            'bindings': obj.bindings,
            'parent': serialize_voxlogica_object(obj.parent) if obj.parent else None
        }
    
    else:
        # Handle other types or return as-is
        return obj
```

### DOT Graph Styling

```python
def apply_dot_styling(
    workplan: WorkPlan,
    style: str = "default"
) -> str:
    """Apply styling to DOT graph based on operation types."""
    
    if style == "default":
        return workplan_to_dot(workplan)
    
    elif style == "colored":
        # Color nodes by operation type
        color_map = {
            'load': 'lightblue',
            'save': 'lightgreen', 
            'compute': 'lightyellow',
            'aggregate': 'lightcoral'
        }
        
        # Generate DOT with colors
        lines = ['digraph WorkPlan {', '  rankdir=TB;']
        
        for node_id, operation in workplan.operations.items():
            op_type = classify_operation(operation)
            color = color_map.get(op_type, 'white')
            
            lines.append(
                f'  "{node_id}" [label="{operation.primitive_name}", '
                f'fillcolor="{color}", style=filled];'
            )
        
        # Add edges...
        return '\n'.join(lines + ['}'])
    
    elif style == "hierarchical":
        # Group operations by execution level
        return create_hierarchical_dot(workplan)
```

### Format Validation

```python
def validate_json_format(data: Dict[str, Any]) -> List[str]:
    """Validate JSON data represents valid VoxLogicA structure."""
    
    errors = []
    
    if 'type' not in data:
        errors.append("Missing 'type' field")
        return errors
    
    if data['type'] == 'WorkPlan':
        required_fields = ['operations', 'goals', 'dependencies']
        for field in required_fields:
            if field not in data:
                errors.append(f"WorkPlan missing required field: {field}")
        
        # Validate operations
        if 'operations' in data:
            for op_id, op_data in data['operations'].items():
                op_errors = validate_operation_json(op_data)
                errors.extend(f"Operation {op_id}: {err}" for err in op_errors)
    
    elif data['type'] == 'Operation':
        errors.extend(validate_operation_json(data))
    
    return errors

def validate_operation_json(data: Dict[str, Any]) -> List[str]:
    """Validate operation JSON data."""
    
    errors = []
    required_fields = ['primitive_name', 'arguments', 'node_id', 'content_hash']
    
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")
    
    return errors
```

## Dependencies

### Internal Dependencies
- `voxlogica.reducer` - WorkPlan and Operation definitions
- `voxlogica.parser` - Expression and AST structures

### External Dependencies
- `json` - JSON serialization and parsing
- `pathlib` - File path operations
- `typing` - Type annotations

## Usage Examples

### JSON Conversion
```python
from voxlogica.converters import workplan_to_json, json_to_workplan
from voxlogica.converters.json_converter import WorkPlanJSONEncoder

# Convert workplan to JSON
workplan = create_sample_workplan()
json_str = workplan_to_json(workplan, pretty=True)
print("Workplan as JSON:")
print(json_str)

# Save to file
with open("workplan.json", "w") as f:
    json.dump(workplan, f, cls=WorkPlanJSONEncoder, indent=2)

# Load from file
with open("workplan.json", "r") as f:
    loaded_workplan = json_to_workplan(f.read())
```

### DOT Graph Generation
```python
from voxlogica.converters import workplan_to_dot, save_dot_file

# Generate DOT graph
workplan = create_sample_workplan()
dot_content = workplan_to_dot(
    workplan,
    include_goals=True,
    include_hashes=True,
    layout="LR"  # Left to Right layout
)

print("Workplan as DOT graph:")
print(dot_content)

# Save DOT file and render with Graphviz
save_dot_file(workplan, "workplan.dot")

# Render to PNG (requires Graphviz installed)
import subprocess
subprocess.run(["dot", "-Tpng", "workplan.dot", "-o", "workplan.png"])
```

### Custom Format Registration
```python
from voxlogica.converters import ConverterRegistry

def workplan_to_yaml(workplan: WorkPlan) -> str:
    """Convert workplan to YAML format."""
    import yaml
    
    data = {
        'operations': {k: v.__dict__ for k, v in workplan.operations.items()},
        'goals': {k: v.__dict__ for k, v in workplan.goals.items()},
        'dependencies': {k: list(v) for k, v in workplan.dependencies.items()}
    }
    
    return yaml.dump(data, default_flow_style=False)

# Register YAML converter
ConverterRegistry.register_converter(
    'yaml',
    encoder=workplan_to_yaml,
    extensions=['.yaml', '.yml']
)

# Use registered converter
yaml_output = ConverterRegistry.get_converter('yaml')['encode'](workplan)
```

### Format Auto-Detection
```python
def save_workplan(workplan: WorkPlan, filename: str, **options) -> None:
    """Save workplan in format determined by file extension."""
    
    format_name = ConverterRegistry.detect_format(filename)
    
    if not format_name:
        raise ValueError(f"Unsupported file format for: {filename}")
    
    converter = ConverterRegistry.get_converter(format_name)
    if not converter['encode']:
        raise ValueError(f"Format {format_name} does not support encoding")
    
    content = converter['encode'](workplan, **options)
    
    with open(filename, 'w') as f:
        f.write(content)

# Usage examples
save_workplan(workplan, "output.json")  # Auto-detects JSON
save_workplan(workplan, "graph.dot")    # Auto-detects DOT
save_workplan(workplan, "data.yaml")    # Auto-detects YAML (if registered)
```

## Performance Considerations

### JSON Serialization Performance
- **Streaming**: Large workplans can be serialized incrementally
- **Compression**: JSON output can be compressed for storage
- **Caching**: Serialized results can be cached based on content hash

### Memory Efficiency
- **Lazy Serialization**: Objects are serialized only when accessed
- **Reference Sharing**: Shared objects are serialized once with references
- **Garbage Collection**: Temporary serialization data is cleaned up

### DOT Graph Optimization
- **Simplification**: Large graphs can be simplified for visualization
- **Clustering**: Related operations can be grouped into clusters
- **Layout Caching**: Graph layouts can be cached for repeated use

## Advanced Usage Patterns

### Incremental JSON Updates
```python
def update_workplan_json(json_file: str, updates: Dict[str, Any]) -> None:
    """Update existing JSON workplan file incrementally."""
    
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    # Apply updates
    for key, value in updates.items():
        if key in data:
            if isinstance(data[key], dict) and isinstance(value, dict):
                data[key].update(value)
            else:
                data[key] = value
    
    with open(json_file, 'w') as f:
        json.dump(data, f, cls=WorkPlanJSONEncoder, indent=2)
```

### Multi-Format Export
```python
def export_workplan_multi_format(
    workplan: WorkPlan,
    base_filename: str,
    formats: List[str] = ['json', 'dot']
) -> List[str]:
    """Export workplan to multiple formats."""
    
    exported_files = []
    
    for format_name in formats:
        converter = ConverterRegistry.get_converter(format_name)
        if not converter or not converter['encode']:
            continue
        
        extensions = converter.get('extensions', [f'.{format_name}'])
        filename = f"{base_filename}{extensions[0]}"
        
        content = converter['encode'](workplan)
        
        with open(filename, 'w') as f:
            f.write(content)
        
        exported_files.append(filename)
    
    return exported_files
```

### Custom Serialization Hooks
```python
class CustomWorkPlanEncoder(WorkPlanJSONEncoder):
    """Custom encoder with hooks for domain-specific data."""
    
    def __init__(self, include_metadata: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.include_metadata = include_metadata
    
    def default(self, obj: Any) -> Any:
        # Custom handling for specific types
        if hasattr(obj, '__voxlogica_serialize__'):
            return obj.__voxlogica_serialize__()
        
        # Apply metadata filtering
        result = super().default(obj)
        
        if isinstance(result, dict) and not self.include_metadata:
            result.pop('metadata', None)
        
        return result
```

## Integration Points

### With CLI System
Converters are integrated into the CLI for output formatting:

```python
# In main.py
@cli_app.command()
def export(
    input_file: str,
    output_file: str,
    format: str = typer.Option(None, help="Output format (auto-detected if not specified)")
):
    """Export workplan to different format."""
    
    # Load workplan
    workplan = load_workplan(input_file)
    
    # Determine format
    if not format:
        format = ConverterRegistry.detect_format(output_file)
    
    # Convert and save
    save_workplan(workplan, output_file, format=format)
```

### With Execution Engine
Converters provide debugging and monitoring outputs:

```python
# In execution.py
def save_execution_trace(workplan: WorkPlan, execution_results: Dict[str, Any]):
    """Save execution trace in multiple formats for analysis."""
    
    # Enhanced workplan with execution metadata
    enhanced_workplan = add_execution_metadata(workplan, execution_results)
    
    # Export trace in multiple formats
    export_workplan_multi_format(enhanced_workplan, "execution_trace")
```

### With Storage System
Converters handle persistent storage formats:

```python
# In storage.py
def serialize_for_storage(obj: Any) -> bytes:
    """Serialize object for storage using appropriate converter."""
    
    if isinstance(obj, WorkPlan):
        json_str = workplan_to_json(obj, pretty=False)
        return json_str.encode('utf-8')
    else:
        return pickle.dumps(obj)
```
