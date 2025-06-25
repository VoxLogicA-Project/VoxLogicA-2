# VoxLogicA Closure System Documentation

## Overview

VoxLogicA implements a closure-based execution model for distributed for-loops that provides proper lexical scoping, environment capture, and distributed execution support. This system enables complex nested operations while maintaining functional programming principles.

## Core Concepts

### Closures in VoxLogicA

A **closure** in VoxLogicA captures:
1. **Variable**: The parameter name (e.g., 'i' in `for i in range(0,3)`)
2. **Expression**: The loop body as an AST Expression object
3. **Environment**: The lexical environment at closure creation time
4. **Workplan**: Reference to the current workplan for dependency resolution

```python
@dataclass  
class ClosureValue:
    variable: str          # Parameter name (e.g., 'i')
    expression: Expression # AST expression (not string)
    environment: Environment # Captured environment
    workplan: WorkPlan    # Reference for context
```

### Environment Capture

VoxLogicA environments are immutable and hierarchical:
- Variables are bound in nested scopes
- Outer scope variables remain accessible
- Variable shadowing works correctly
- Environment capture preserves lexical scope

```voxlogica
let x = 10
let results = for i in range(0,3) do +(i, x)  // x is captured from outer scope
```

## For-Loop Execution Model

### 1. Closure Creation

When a for-loop is encountered:
```voxlogica
for i in range(0,3) do +(i, 1)
```

The system creates:
```python
closure = ClosureValue(
    variable='i',
    expression=AST(+(i,1)),  # Parsed expression, not string
    environment=current_env, # Captured at creation time
    workplan=current_workplan
)
```

### 2. Distributed Execution

The closure is mapped over the iterable using Dask:
```python
# In dask_map.py
result_bag = input_bag.map(closure)
```

### 3. Closure Execution

For each value in the iteration:
```python
def __call__(self, value):
    # 1. Bind loop variable to current value
    value_node = ConstantValue(value=value)
    value_id = self.workplan.add_node(value_node)
    value_dval = OperationVal(value_id)
    new_env = self.environment.bind(self.variable, value_dval)
    
    # 2. Reduce expression in new environment
    result_id = reduce_expression(new_env, self.workplan, self.expression)
    
    # 3. Execute operation directly
    return execute_operation_directly(result_id)
```

## Direct Operation Execution

### Bypassing the Execution Engine

For efficiency, closures execute operations directly rather than using the full execution engine:

1. **Dependency Resolution**: Copy required nodes from workplan
2. **Argument Resolution**: Extract and resolve operation arguments
3. **Argument Mapping**: Convert numeric keys to semantic names
4. **Direct Execution**: Call primitive function directly

### Argument Mapping

VoxLogicA maps numeric argument keys to semantic names for better primitive interfaces:

```python
# Binary operators: +(left, right)
if operator in ['+', 'addition', 'add']:
    if '0' in args and '1' in args:
        mapped_args = {'left': args['0'], 'right': args['1']}
```

Supported mappings:
- **Arithmetic operators** (`+`, `-`, `*`, `/`): `'0'` → `'left'`, `'1'` → `'right'`
- **Comparison operators**: Similar left/right mapping
- **Other operators**: Use original numeric keys

## Nested For-Loops

### Environment Nesting

VoxLogicA correctly handles nested for-loops with proper environment scoping:

```voxlogica
let dataset = for i in range(0,10) do BinaryThreshold(img, 100+i, 99999, 1, 0)
let processed = for img in dataset do MinimumMaximumImageFilter(img)
```

Each for-loop:
1. Creates its own closure with captured environment
2. Maintains access to outer scope variables
3. Properly scopes the loop variable
4. Enables complex nested operations

### Variable Shadowing

Inner loops correctly shadow outer variables:

```voxlogica
let i = 42
let results = for i in range(0,3) do      // Shadows outer 'i'
    for j in range(0,2) do +(i, j)       // Uses loop 'i', not outer 'i'
```

## Storage and Serialization

### Content-Addressed Storage

Closures are stored with special handling for non-serializable components:

```python
def _compute_node_id(self, node):
    if isinstance(node, ClosureValue):
        # Create serializable representation
        serializable_dict = {
            'type': 'ClosureValue',
            'variable': node.variable,
            'expression_str': node.expression.to_syntax(),
            # Environment and workplan excluded from hash
        }
        return sha256_hash(serializable_dict)
```

### Distributed Storage

- **Serializable results**: Stored in SQLite database
- **Non-serializable results** (closures, complex objects): Stored in memory cache
- **Cross-worker communication**: Handled automatically by Dask serialization

## Error Handling and Fallback

### Graceful Degradation

When closure execution encounters issues:
1. **Unresolved dependencies**: Return original value as fallback
2. **Operation failures**: Log warning and return original value
3. **Argument mapping failures**: Attempt with original numeric keys

### Debugging Support

Closure execution includes comprehensive logging:
- Variable binding information
- Expression reduction details
- Operation execution status
- Fallback trigger conditions

## Performance Characteristics

### Efficiency Optimizations

1. **Direct execution**: Bypasses full execution engine overhead
2. **Minimal workplan copying**: Only copies required dependencies
3. **Cached argument resolution**: Reuses resolved arguments where possible
4. **Lazy evaluation**: Operations only executed when needed

### Scalability

- **Distributed execution**: Works correctly across Dask workers
- **Memory efficiency**: Non-serializable objects stay in memory cache
- **Network optimization**: Minimal data transfer between workers

## Best Practices

### For Primitive Implementers

1. **Stateless operations**: Implement primitives as pure functions
2. **Argument validation**: Check for required arguments with clear error messages
3. **Type flexibility**: Handle both numeric and appropriate string keys
4. **Error propagation**: Use appropriate exception types for different failures

### For Language Users

1. **Variable scoping**: Understand lexical scoping rules in nested for-loops
2. **Performance considerations**: Be aware of closure creation overhead
3. **Error handling**: Expect graceful fallback behavior in complex scenarios
4. **Debugging**: Use logging to understand closure execution flow

## Future Enhancements

### Planned Improvements

1. **Enhanced dependency resolution**: Handle complex cross-loop dependencies
2. **Performance optimizations**: Cache closure compilations
3. **Extended argument mapping**: Support more operator types
4. **Debugging tools**: Better introspection of closure state
5. **Serialization improvements**: More efficient closure serialization

### Research Directions

1. **Closure optimization**: Compile closures to more efficient representations
2. **Static analysis**: Detect closure dependencies at compile time
3. **Parallel closure execution**: Execute independent closures in parallel
4. **Memory management**: Better handling of large closure datasets
