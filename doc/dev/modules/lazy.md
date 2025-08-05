# lazy.py - Lazy Compilation Infrastructure

## Purpose

The `lazy.py` module provides the infrastructure for deferred compilation and lazy evaluation in VoxLogicA-2. It enables efficient handling of complex expressions with parameter substitution and supports optimization through delayed computation.

## Architecture

### Core Components

#### 1. Lazy Compilation
- **Deferred Expression Compilation**: Expressions are compiled only when dependencies are satisfied
- **Parameter Binding**: Runtime substitution of parameters in compiled expressions
- **Dependency Tracking**: Automatic tracking of what results are needed for compilation

#### 2. For Loop Expansion
- **Dask Bag Integration**: Efficient handling of parallel for-loop iterations
- **Dynamic Expansion**: Loop bodies are expanded based on runtime iterable values
- **Memory Efficient**: Large loops are processed without materializing all iterations

#### 3. Compilation Coordination
- **Readiness Checking**: Determine when expressions can be compiled
- **Incremental Compilation**: Support for partial compilation as dependencies become available
- **Error Propagation**: Proper error handling during deferred compilation

### Key Classes and Data Structures

#### `LazyCompilation`
Represents a deferred compilation of an expression with parameter substitution.

```python
@dataclass
class LazyCompilation:
    expression: Expression          # AST expression to compile (e.g., f(x))
    environment: Environment        # Environment at compilation time
    parameter_bindings: Dict[str, NodeId]  # Runtime parameter substitutions
    compilation_id: str = ""        # For debugging and tracking
    
    def can_compile(self, available_results: Dict[NodeId, Any]) -> bool:
        """Check if all parameter dependencies are satisfied."""
        return all(dep in available_results for dep in self.parameter_bindings.values())
```

#### `ForLoopCompilation`
Represents a deferred compilation of a for loop with Dask bag expansion.

```python
@dataclass
class ForLoopCompilation:
    variable: str                   # Loop variable name
    iterable_expr: Expression       # Expression that evaluates to an iterable
    body_expr: Expression          # Loop body expression
    environment: Environment        # Environment at compilation time
    stack: Stack                   # Call stack for error reporting
    
    def __str__(self) -> str:
        return f"for {self.variable} in {self.iterable_expr} do {self.body_expr}"
```

## Implementation Details

### Lazy Compilation Process

1. **Expression Capture**: When an expression cannot be immediately compiled, it's wrapped in a `LazyCompilation`
2. **Dependency Analysis**: Identify which results must be available before compilation can proceed
3. **Readiness Checking**: Periodically check if dependencies are satisfied
4. **Deferred Compilation**: Compile expression when all dependencies are available
5. **Result Integration**: Integrate compiled results into the main workplan

### Parameter Binding Strategy

```python
def bind_parameters(expr: Expression, bindings: Dict[str, NodeId]) -> Expression:
    """Substitute parameters in expression with actual node IDs."""
    if isinstance(expr, ECall):
        if expr.identifier in bindings:
            # Replace parameter with bound value
            return create_node_reference(bindings[expr.identifier])
        else:
            # Recursively bind arguments
            bound_args = [bind_parameters(arg, bindings) for arg in expr.arguments]
            return ECall(expr.position, expr.identifier, bound_args)
    
    elif isinstance(expr, ELet):
        # Handle let bindings with proper scoping
        bound_value = bind_parameters(expr.value, bindings)
        # Remove binding from scope for body
        body_bindings = {k: v for k, v in bindings.items() if k != expr.identifier}
        bound_body = bind_parameters(expr.body, body_bindings)
        return ELet(expr.position, expr.identifier, bound_value, bound_body)
    
    # ... handle other expression types
```

### For Loop Expansion

```python
def expand_for_loop(loop: ForLoopCompilation, iterable_values: List[Any]) -> List[NodeId]:
    """Expand for loop into individual iterations."""
    iteration_nodes = []
    
    for i, value in enumerate(iterable_values):
        # Create environment with loop variable bound
        iteration_env = loop.environment.bind(loop.variable, create_value_node(value))
        
        # Compile loop body with bound environment
        body_node = compile_expression(loop.body_expr, iteration_env)
        iteration_nodes.append(body_node)
    
    return iteration_nodes
```

## Dependencies

### Internal Dependencies
- `voxlogica.parser` - Expression and AST definitions
- `voxlogica.reducer` - Environment and compilation infrastructure

### External Dependencies
- `dataclasses` - Data structure definitions
- `typing` - Type annotations

## Usage Examples

### Basic Lazy Compilation
```python
from voxlogica.lazy import LazyCompilation
from voxlogica.parser import parse_expression

# Create expression that depends on runtime parameters
expr = parse_expression("add(x, y)")
env = Environment()
bindings = {"x": "node_123", "y": "node_456"}

# Create lazy compilation
lazy_comp = LazyCompilation(
    expression=expr,
    environment=env,
    parameter_bindings=bindings,
    compilation_id="add_operation"
)

# Check if ready to compile
available_results = {"node_123": 42, "node_456": 24}
if lazy_comp.can_compile(available_results):
    # Proceed with compilation
    result_node = compile_lazy_expression(lazy_comp)
```

### For Loop Handling
```python
from voxlogica.lazy import ForLoopCompilation

# Create for loop compilation
loop_expr = parse_expression("for i in range(10) do square(i)")
for_loop = ForLoopCompilation(
    variable="i",
    iterable_expr=parse_expression("range(10)"),
    body_expr=parse_expression("square(i)"),
    environment=env,
    stack=[]
)

# Expand when iterable is available
iterable_values = list(range(10))
iteration_nodes = expand_for_loop(for_loop, iterable_values)
print(f"Generated {len(iteration_nodes)} iterations")
```

### Dependency Management
```python
def track_lazy_dependencies(lazy_comps: List[LazyCompilation]) -> Dict[str, Set[NodeId]]:
    """Track dependencies for all lazy compilations."""
    dependencies = {}
    
    for comp in lazy_comps:
        comp_deps = set(comp.parameter_bindings.values())
        dependencies[comp.compilation_id] = comp_deps
    
    return dependencies

def find_ready_compilations(
    lazy_comps: List[LazyCompilation], 
    available_results: Dict[NodeId, Any]
) -> List[LazyCompilation]:
    """Find compilations ready to execute."""
    ready = []
    
    for comp in lazy_comps:
        if comp.can_compile(available_results):
            ready.append(comp)
    
    return ready
```

## Performance Considerations

### Memory Efficiency
- **Minimal Storage**: Lazy compilations store only essential information
- **Shared Environments**: Environment structures are shared to reduce memory usage
- **Garbage Collection**: Completed lazy compilations are automatically cleaned up

### Compilation Optimization
- **Batch Compilation**: Multiple ready compilations are processed together
- **Common Subexpression**: Shared expressions in lazy compilations are deduplicated
- **Incremental Processing**: Results are processed as they become available

### Scalability Features
- **Parallel Compilation**: Independent lazy compilations can be processed in parallel
- **Streaming Support**: Large numbers of lazy compilations can be processed incrementally
- **Resource Management**: Memory usage is controlled through compilation batching

## Advanced Usage Patterns

### Conditional Lazy Compilation
```python
def create_conditional_lazy_compilation(
    condition_expr: Expression,
    then_expr: Expression,
    else_expr: Expression,
    env: Environment
) -> LazyCompilation:
    """Create lazy compilation for conditional expressions."""
    
    # Wrap conditional logic in a single expression
    conditional = EConditional(condition_expr, then_expr, else_expr)
    
    return LazyCompilation(
        expression=conditional,
        environment=env,
        parameter_bindings={},
        compilation_id=f"conditional_{generate_id()}"
    )
```

### Nested Lazy Compilations
```python
def handle_nested_lazy_compilations(
    outer_comp: LazyCompilation,
    inner_comps: List[LazyCompilation]
) -> LazyCompilation:
    """Handle expressions with nested lazy compilations."""
    
    # Create dependencies between outer and inner compilations
    nested_bindings = {}
    for inner in inner_comps:
        nested_bindings[inner.compilation_id] = inner
    
    return LazyCompilation(
        expression=outer_comp.expression,
        environment=outer_comp.environment,
        parameter_bindings=outer_comp.parameter_bindings,
        compilation_id=f"nested_{outer_comp.compilation_id}",
        nested_compilations=nested_bindings
    )
```

### Dynamic Parameter Binding
```python
def update_parameter_bindings(
    lazy_comp: LazyCompilation,
    new_bindings: Dict[str, NodeId]
) -> LazyCompilation:
    """Update parameter bindings for a lazy compilation."""
    
    updated_bindings = {**lazy_comp.parameter_bindings, **new_bindings}
    
    return LazyCompilation(
        expression=lazy_comp.expression,
        environment=lazy_comp.environment,
        parameter_bindings=updated_bindings,
        compilation_id=lazy_comp.compilation_id
    )
```

## Integration Points

### With Reducer
Lazy compilations are created during reduction when dependencies are not available:

```python
# In reducer.py
def compile_expression(expr: Expression, env: Environment) -> Union[NodeId, LazyCompilation]:
    if all_dependencies_available(expr, env):
        return immediate_compilation(expr, env)
    else:
        return create_lazy_compilation(expr, env)
```

### With Execution Engine
The execution engine handles lazy compilations during workplan execution:

```python
# In execution.py
def process_lazy_compilations(
    workplan: WorkPlan,
    available_results: Dict[NodeId, Any]
) -> List[NodeId]:
    """Process ready lazy compilations during execution."""
    
    ready_comps = find_ready_compilations(workplan.lazy_compilations, available_results)
    compiled_nodes = []
    
    for comp in ready_comps:
        node_id = compile_lazy_expression(comp)
        compiled_nodes.append(node_id)
        workplan.remove_lazy_compilation(comp)
    
    return compiled_nodes
```

### With Storage System
Lazy compilations can be cached based on their content:

```python
def cache_lazy_compilation_result(
    comp: LazyCompilation,
    result: Any,
    storage: StorageBackend
) -> None:
    """Cache the result of a lazy compilation."""
    
    cache_key = compute_lazy_compilation_hash(comp)
    storage.put(cache_key, result)

def retrieve_cached_lazy_result(
    comp: LazyCompilation,
    storage: StorageBackend
) -> Optional[Any]:
    """Retrieve cached result for lazy compilation."""
    
    cache_key = compute_lazy_compilation_hash(comp)
    return storage.get(cache_key)
```

## Debugging and Monitoring

### Compilation Tracking
```python
def track_lazy_compilation_progress(
    lazy_comps: List[LazyCompilation]
) -> Dict[str, str]:
    """Track the progress of lazy compilations."""
    
    status = {}
    for comp in lazy_comps:
        if comp.can_compile(get_available_results()):
            status[comp.compilation_id] = "ready"
        else:
            missing_deps = get_missing_dependencies(comp)
            status[comp.compilation_id] = f"waiting_for_{len(missing_deps)}_deps"
    
    return status
```

### Performance Monitoring
```python
def measure_lazy_compilation_performance(
    comp: LazyCompilation
) -> CompilationMetrics:
    """Measure performance of lazy compilation."""
    
    start_time = time.time()
    result = compile_lazy_expression(comp)
    end_time = time.time()
    
    return CompilationMetrics(
        compilation_id=comp.compilation_id,
        compilation_time=end_time - start_time,
        expression_complexity=calculate_expression_complexity(comp.expression),
        parameter_count=len(comp.parameter_bindings)
    )
```
