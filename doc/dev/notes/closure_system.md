# Closure System and Environment Management

## Overview

VoxLogicA-2 implements a sophisticated closure-based execution model that provides proper lexical scoping, environment capture, and distributed execution support. This system enables complex nested operations while maintaining functional programming principles.

## Core Concepts

### Closures in VoxLogicA

A **closure** in VoxLogicA captures the complete execution context needed for deferred evaluation:

```python
@dataclass  
class ClosureValue:
    variable: str          # Parameter name (e.g., 'i' in for i in range(0,3))
    expression: Expression # AST expression (not string)
    environment: Environment # Captured lexical environment
    workplan: WorkPlan    # Reference for dependency resolution
```

### Environment Architecture

VoxLogicA environments are immutable and hierarchical, enabling safe parallel execution:

```python
@dataclass
class Environment:
    bindings: Dict[str, NodeId]      # Variable name to node ID mappings
    parent: Optional[Environment]     # Parent scope for lexical scoping
    
    def bind(self, name: str, value: NodeId) -> Environment:
        """Create new environment with additional binding."""
        new_bindings = {**self.bindings, name: value}
        return Environment(new_bindings, self.parent)
    
    def lookup(self, name: str) -> Optional[NodeId]:
        """Lookup variable in current or parent scopes."""
        if name in self.bindings:
            return self.bindings[name]
        elif self.parent:
            return self.parent.lookup(name)
        else:
            return None
    
    def extend(self) -> Environment:
        """Create child environment for nested scope."""
        return Environment({}, self)
```

## For-Loop Execution Model

### 1. Closure Creation

When a for-loop is encountered:
```voxlogica
let x = 10
for i in range(0,3) do add(i, x)
```

The system creates a closure that captures the lexical environment:

```python
closure = ClosureValue(
    variable="i",
    expression=parse_expression("add(i, x)"),
    environment=current_env,  # Contains binding for x=10
    workplan=current_workplan
)
```

### 2. Distributed Expansion

For distributed execution, closures are expanded into individual tasks:

```python
def expand_closure_for_dask(closure: ClosureValue, iterable_values: List[Any]) -> List[Future]:
    """Expand closure into Dask delayed computations."""
    
    futures = []
    for value in iterable_values:
        # Create environment with loop variable bound
        iteration_env = closure.environment.bind(closure.variable, create_node_for_value(value))
        
        # Create delayed computation for this iteration
        delayed_computation = delayed(execute_expression)(
            closure.expression,
            iteration_env,
            closure.workplan
        )
        
        futures.append(delayed_computation)
    
    return futures
```

### 3. Environment Capture Examples

#### Simple Variable Capture
```voxlogica
let multiplier = 5
let results = for i in range(1, 4) do multiply(i, multiplier)
```

The closure captures `multiplier=5` from the outer scope:
- Iteration 1: `multiply(1, 5)` → 5
- Iteration 2: `multiply(2, 5)` → 10  
- Iteration 3: `multiply(3, 5)` → 15

#### Nested Environment Capture
```voxlogica
let base = 10
let transform = for x in range(1, 3) do
    let offset = 100
    for y in range(1, 3) do add(add(base, x), add(offset, y))
```

The inner closure captures both `base` and `offset`:
- Inner environment: `{y: value, offset: 100}`
- Outer environment: `{x: value, base: 10}`
- Full context available to inner expression

### 4. Dependency Management

Closures maintain proper dependency relationships in distributed execution:

```python
def resolve_closure_dependencies(closure: ClosureValue) -> Set[NodeId]:
    """Resolve all dependencies for closure execution."""
    
    dependencies = set()
    
    # Dependencies from captured environment
    for var_name, node_id in closure.environment.bindings.items():
        dependencies.add(node_id)
    
    # Dependencies from parent environments
    parent = closure.environment.parent
    while parent:
        dependencies.update(parent.bindings.values())
        parent = parent.parent
    
    # Dependencies from expression
    expr_deps = analyze_expression_dependencies(closure.expression)
    dependencies.update(expr_deps)
    
    return dependencies
```

## Advanced Environment Features

### Environment Merging

For module imports and composition:

```python
def merge_environments(env1: Environment, env2: Environment) -> Environment:
    """Merge two environments, with env2 taking precedence."""
    
    merged_bindings = {**env1.bindings, **env2.bindings}
    return Environment(merged_bindings, env1.parent)
```

### Environment Serialization

For distributed execution across machines:

```python
def serialize_environment(env: Environment) -> Dict[str, Any]:
    """Serialize environment for distributed execution."""
    
    return {
        'bindings': env.bindings,
        'parent': serialize_environment(env.parent) if env.parent else None
    }

def deserialize_environment(data: Dict[str, Any]) -> Environment:
    """Deserialize environment from distributed worker."""
    
    parent = deserialize_environment(data['parent']) if data['parent'] else None
    return Environment(data['bindings'], parent)
```

### Environment Optimization

For performance optimization:

```python
def optimize_environment(env: Environment) -> Environment:
    """Optimize environment by flattening unused parent chains."""
    
    # Collect all accessible bindings
    all_bindings = {}
    current = env
    
    while current:
        # Add bindings that don't conflict with inner scopes
        for name, value in current.bindings.items():
            if name not in all_bindings:
                all_bindings[name] = value
        current = current.parent
    
    # Return flattened environment
    return Environment(all_bindings, None)
```

## Error Handling in Closures

### Scope Resolution Errors

```python
class ScopeError(VoxLogicAError):
    """Error in variable scope resolution."""
    pass

def safe_variable_lookup(env: Environment, var_name: str, stack: Stack) -> NodeId:
    """Safely lookup variable with detailed error reporting."""
    
    result = env.lookup(var_name)
    if result is None:
        # Generate helpful error message
        available_vars = get_available_variables(env)
        error_msg = f"Variable '{var_name}' not in scope. Available: {available_vars}"
        raise ScopeError(error_msg)
    
    return result
```

### Closure Execution Errors

```python
def execute_closure_safely(
    closure: ClosureValue,
    iteration_value: Any,
    stack: Stack
) -> Any:
    """Execute closure with comprehensive error handling."""
    
    try:
        # Bind iteration variable
        iteration_env = closure.environment.bind(closure.variable, iteration_value)
        
        # Execute expression
        return execute_expression(closure.expression, iteration_env, stack)
        
    except Exception as e:
        # Enhance error with closure context
        context = {
            'closure_variable': closure.variable,
            'iteration_value': iteration_value,
            'available_bindings': list(closure.environment.bindings.keys())
        }
        
        raise ClosureExecutionError(f"Closure execution failed: {e}", context) from e
```

## Performance Considerations

### Environment Sharing

Environments use structural sharing to minimize memory usage:

```python
def create_child_environment(parent: Environment, new_bindings: Dict[str, NodeId]) -> Environment:
    """Create child environment with structural sharing."""
    
    # Parent environment is shared, not copied
    return Environment(new_bindings, parent)
```

### Closure Optimization

Large closures can be optimized by analyzing variable usage:

```python
def optimize_closure(closure: ClosureValue) -> ClosureValue:
    """Optimize closure by removing unused environment bindings."""
    
    # Analyze which variables are actually used
    used_variables = analyze_variable_usage(closure.expression)
    
    # Create minimal environment with only used variables
    minimal_bindings = {
        name: value for name, value in closure.environment.bindings.items()
        if name in used_variables
    }
    
    minimal_env = Environment(minimal_bindings, closure.environment.parent)
    
    return ClosureValue(
        closure.variable,
        closure.expression,
        minimal_env,
        closure.workplan
    )
```

### Distributed Closure Caching

```python
def cache_closure_results(closure: ClosureValue, iterable_values: List[Any]) -> Dict[Any, Any]:
    """Cache closure execution results for repeated iterations."""
    
    cache_key = compute_closure_hash(closure)
    cached_results = storage.get(f"closure_results_{cache_key}")
    
    if cached_results:
        return cached_results
    
    # Execute and cache results
    results = {}
    for value in iterable_values:
        result = execute_closure(closure, value)
        results[value] = result
    
    storage.put(f"closure_results_{cache_key}", results)
    return results
```

## Integration with Dask

### Closure Serialization for Dask

```python
def make_closure_dask_compatible(closure: ClosureValue) -> Callable:
    """Create Dask-compatible function from closure."""
    
    def dask_closure_executor(iteration_value: Any) -> Any:
        # Recreate environment with iteration value
        execution_env = closure.environment.bind(closure.variable, iteration_value)
        
        # Execute expression in proper context
        return execute_expression_with_workplan(
            closure.expression,
            execution_env,
            closure.workplan
        )
    
    return dask_closure_executor
```

### Parallel Closure Execution

```python
def execute_closure_parallel(
    closure: ClosureValue,
    iterable_values: List[Any],
    client: Client
) -> List[Any]:
    """Execute closure in parallel using Dask."""
    
    # Create Dask-compatible executor
    executor = make_closure_dask_compatible(closure)
    
    # Submit parallel tasks
    futures = []
    for value in iterable_values:
        future = client.submit(executor, value)
        futures.append(future)
    
    # Gather results
    results = client.gather(futures)
    return results
```

## Testing Closure System

### Unit Tests for Environment

```python
def test_environment_binding():
    env = Environment()
    env = env.bind("x", "node_123")
    
    assert env.lookup("x") == "node_123"
    assert env.lookup("y") is None

def test_environment_hierarchy():
    parent = Environment().bind("x", "node_123")
    child = parent.extend().bind("y", "node_456")
    
    assert child.lookup("x") == "node_123"  # From parent
    assert child.lookup("y") == "node_456"  # From child
    assert parent.lookup("y") is None       # Not in parent
```

### Integration Tests for Closures

```python
def test_closure_execution():
    # Create test closure
    expr = parse_expression("add(i, 10)")
    env = Environment().bind("base", "node_base")
    closure = ClosureValue("i", expr, env, test_workplan)
    
    # Execute with different values
    result1 = execute_closure(closure, 5)   # Should compute add(5, 10)
    result2 = execute_closure(closure, 7)   # Should compute add(7, 10)
    
    assert result1 == 15
    assert result2 == 17
```
