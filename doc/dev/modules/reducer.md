# reducer.py - Core Reduction Engine

## Purpose

The `reducer.py` module implements the core reduction engine for VoxLogicA-2, responsible for compiling VoxLogicA programs into executable workplans. It manages environments, handles lazy compilation, and orchestrates the transformation from high-level language constructs to computational graphs.

## Architecture

### Core Components

#### 1. WorkPlan Management
- **WorkPlan Class**: Central data structure representing computational graphs
- **Operation Nodes**: Individual computation units with dependencies
- **Goal Tracking**: Management of computation objectives and their resolution

#### 2. Environment System
- **Variable Binding**: Scoped variable management and resolution
- **Closure Handling**: Support for lexical closures and captured variables
- **Stack Management**: Call stack tracking for error reporting

#### 3. Reduction Pipeline
- **AST Processing**: Transformation of parsed expressions into operations
- **Dependency Analysis**: Automatic dependency graph construction
- **Optimization**: Dead code elimination and operation fusion

### Key Classes and Data Structures

#### `WorkPlan`
Central data structure representing a computational graph.

```python
@dataclass
class WorkPlan:
    operations: Dict[NodeId, Operation]
    goals: Dict[str, Goal]
    dependencies: Dict[NodeId, Set[NodeId]]
    
    def add_operation(self, op: Operation) -> NodeId
    def add_goal(self, name: str, node_id: NodeId) -> None
    def compute_dependencies(self) -> None
```

#### `Operation`
Represents a single computational operation.

```python
@dataclass
class Operation:
    primitive_name: str
    arguments: Arguments
    node_id: NodeId
    content_hash: str
    
    def compute_hash(self) -> str
    def get_dependencies(self) -> Set[NodeId]
```

#### `Environment`
Manages variable bindings and scoping.

```python
@dataclass
class Environment:
    bindings: Dict[str, NodeId]
    parent: Optional[Environment]
    
    def bind(self, name: str, value: NodeId) -> Environment
    def lookup(self, name: str) -> Optional[NodeId]
    def extend(self) -> Environment
```

## Implementation Details

### Reduction Algorithm

1. **Expression Analysis**: Parse and analyze VoxLogicA expressions
2. **Environment Resolution**: Resolve variable bindings and closures
3. **Operation Generation**: Create operation nodes for primitive calls
4. **Dependency Construction**: Build dependency graph between operations
5. **Optimization**: Apply optimizations and transformations

### Content-Addressed Operations

Operations are identified by content hashes, enabling:
- **Deduplication**: Identical operations are shared across workplans
- **Caching**: Results can be cached and reused across executions
- **Reproducibility**: Same inputs always produce same operation graphs

```python
def compute_content_hash(primitive_name: str, arguments: Arguments) -> str:
    """Compute deterministic hash for operation deduplication."""
    content = {
        "primitive": primitive_name,
        "arguments": dict(sorted(arguments.items()))
    }
    return hashlib.sha256(canonicaljson.encode_canonical_json(content)).hexdigest()
```

### Lazy Compilation Support

Integration with `lazy.py` for deferred compilation:

```python
@dataclass
class LazyCompilation:
    expression: Expression
    environment: Environment
    parameter_bindings: Dict[str, NodeId]
    
    def can_compile(self, available_results: Dict[NodeId, Any]) -> bool
    def compile_when_ready(self, reducer: Reducer) -> NodeId
```

## Dependencies

### Internal Dependencies
- `voxlogica.parser` - AST definitions and expression parsing
- `voxlogica.lazy` - Lazy compilation infrastructure
- `voxlogica.storage` - Content-addressed storage integration

### External Dependencies
- `hashlib` - Cryptographic hashing for content addressing
- `canonicaljson` - Deterministic JSON serialization
- `dataclasses` - Data structure definitions
- `typing` - Type annotations

## Usage Examples

### Basic Workplan Creation
```python
from voxlogica.reducer import Reducer, Environment

# Initialize reducer with empty environment
reducer = Reducer()
env = Environment()

# Compile expression to workplan
expression = parse_expression("add(x, y)")
workplan = reducer.compile_expression(expression, env)

print(f"Generated {len(workplan.operations)} operations")
```

### Environment Management
```python
# Create environment with bindings
env = Environment()
env = env.bind("x", "node_123")
env = env.bind("y", "node_456")

# Extend environment for nested scope
nested_env = env.extend()
nested_env = nested_env.bind("z", "node_789")

# Lookup variables
x_value = env.lookup("x")  # Returns "node_123"
z_value = env.lookup("z")  # Returns None (not in scope)
```

### Goal Management
```python
# Add computation goals to workplan
workplan.add_goal("result", final_node_id)
workplan.add_goal("intermediate", temp_node_id)

# Execute specific goals
results = executor.execute_goals(workplan, ["result"])
```

## Performance Considerations

### Memory Efficiency
- **Structural Sharing**: Environments share structure to minimize memory usage
- **Lazy Loading**: Operations are only materialized when needed
- **Garbage Collection**: Unreferenced nodes are automatically cleaned up

### Compilation Optimization
- **Common Subexpression Elimination**: Identical subexpressions are shared
- **Dead Code Elimination**: Unused operations are removed
- **Operation Fusion**: Compatible operations are combined for efficiency

### Scalability Features
- **Incremental Compilation**: Support for partial recompilation
- **Parallel Reduction**: Independent expressions can be reduced in parallel
- **Streaming Support**: Large programs can be processed in chunks

## Error Handling and Debugging

### Stack Trace Management
```python
type Stack = list[Tuple[str, str]]  # [(function_name, source_location)]

def push_stack_frame(stack: Stack, function_name: str, location: str) -> Stack:
    """Add frame to call stack for error reporting."""
    return stack + [(function_name, location)]
```

### Validation and Checks
- **Type Consistency**: Ensures operation arguments match expected types
- **Dependency Cycles**: Detects and reports circular dependencies
- **Variable Resolution**: Validates all variables are properly bound

### Error Reporting
```python
def report_compilation_error(stack: Stack, message: str) -> CompilationError:
    """Generate detailed error with stack trace."""
    trace = " -> ".join(f"{func}@{loc}" for func, loc in stack)
    return CompilationError(f"{message}\nStack trace: {trace}")
```

## Advanced Features

### Closure Support
Handles lexical closures with captured variables:

```python
@dataclass
class ClosureValue:
    function_name: str
    captured_environment: Environment
    parameter_names: List[str]
    
    def apply(self, arguments: List[NodeId]) -> NodeId
```

### For Loop Compilation
Special handling for for-loop constructs:

```python
@dataclass
class ForLoopCompilation:
    variable: str
    iterable_expr: Expression
    body_expr: Expression
    environment: Environment
    
    def expand_loop(self, iterable_values: List[Any]) -> List[NodeId]
```

### Import System
Support for modular program composition:

```python
def process_import(import_stmt: Import, env: Environment) -> Environment:
    """Load and integrate imported module."""
    module_path = resolve_import_path(import_stmt.path)
    module_env = compile_module(module_path)
    return merge_environments(env, module_env)
```

## Configuration and Tuning

### Compilation Settings
```python
COMPILATION_CONFIG = {
    'max_inline_depth': 10,
    'enable_cse': True,
    'enable_dce': True,
    'parallel_reduction': True
}
```

### Memory Management
```python
MEMORY_CONFIG = {
    'max_environment_depth': 100,
    'gc_threshold': 10000,
    'enable_structural_sharing': True
}
```

## Integration Points

### With Execution Engine
The reducer generates workplans that are executed by `execution.py`:

```python
# Compilation phase
workplan = reducer.compile_program(program)

# Execution phase  
results = executor.execute_workplan(workplan)
```

### With Storage System
Operations are content-addressed for caching:

```python
# Generate content hash for operation
content_hash = operation.compute_hash()

# Check if result is cached
cached_result = storage.get(content_hash)
if cached_result is None:
    # Execute and cache result
    result = execute_operation(operation)
    storage.put(content_hash, result)
```

### With Feature System
Supports dynamic primitive registration:

```python
# Register new primitive
feature_system.register_primitive("my_operation", MyOperationImpl)

# Use in reduction
operation = Operation("my_operation", arguments, node_id, content_hash)
workplan.add_operation(operation)
```
