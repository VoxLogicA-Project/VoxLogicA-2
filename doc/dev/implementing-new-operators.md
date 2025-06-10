# Implementing New Operators in VoxLogicA-2

## Overview

VoxLogicA-2 uses a dynamic operator loading system that allows new operators to be implemented as Python modules in the `primitives/` directory. Each operator is implemented as a simple Python module with an `execute` function.

## Architecture

The operator system consists of several key components:

1. **Primitive Modules**: Python files in `implementation/python/voxlogica/primitives/` that implement specific operations
2. **PrimitivesLoader**: Dynamically loads operators from the primitives directory (`execution.py`)
3. **Reduction System**: Creates DAG operations that reference these primitives (`reducer.py`)
4. **Execution Engine**: Executes the operations using the loaded primitives

## Implementation Steps

### 1. Create a New Primitive Module

Create a new Python file in `implementation/python/voxlogica/primitives/` with the following structure:

```python
"""
<operator_name> primitive for VoxLogicA-2

Brief description of what this operator does.
"""

def execute(**kwargs):
    """
    Execute the <operator_name> operation
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': arg1, '1': arg2, ...} for multi-argument operations
        
    Returns:
        Result of the operation
        
    Raises:
        ValueError: If operation fails or arguments are invalid
    """
    try:
        # Extract arguments using numeric string keys
        if '0' not in kwargs:
            raise ValueError("<operator_name> requires at least one argument")
        
        arg1 = kwargs['0']
        arg2 = kwargs.get('1')  # Optional second argument
        
        # Implement your operation logic here
        result = your_operation_logic(arg1, arg2)
        return result
    except Exception as e:
        raise ValueError(f"<operator_name> failed: {e}") from e
```

### 2. Naming Conventions

- **Module filename**: Use lowercase with underscores (e.g., `fibonacci_sequence.py`)
- **Operator name**: The operator name in VoxLogicA programs should match the module name
- **Function name**: Always use `execute` as the main function name

### 3. Function Signature

The `execute` function should:
- Use `**kwargs` to accept arguments as keyword arguments with numeric string keys
- Extract arguments using keys `'0'`, `'1'`, etc. corresponding to argument positions
- Return a single value (the result of the operation)
- Raise `ValueError` with descriptive messages for invalid inputs
- Handle type checking and validation internally

**Important**: VoxLogicA passes operation arguments as keyword arguments with numeric string keys (e.g., `{'0': first_arg, '1': second_arg}`). This is part of the DAG argument system that uses string keys for better serialization and extensibility.

### 4. Error Handling

Always include proper error handling:
- Validate input arguments
- Provide meaningful error messages
- Use `ValueError` for operation-specific errors
- Chain exceptions using `from e` to preserve the original exception

## Example: Basic Mathematical Operations

### Addition Operator (`addition.py`)

```python
"""
Addition primitive for VoxLogicA-2

Implements addition operation for numeric types.
"""

def execute(**kwargs):
    """
    Execute addition operation
    
    Args:
        **kwargs: Expected {'0': left, '1': right} for the operands
        
    Returns:
        Sum of left and right
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("Addition requires two arguments")
        
        left = kwargs['0']
        right = kwargs['1']
        result = left + right
        return result
    except Exception as e:
        raise ValueError(f"Addition failed: {e}") from e
```

### Conditional Operator (`conditional.py`)

```python
"""
Conditional primitive for VoxLogicA-2

Implements if-then-else conditional logic.
"""

def execute(**kwargs):
    """
    Execute conditional operation
    
    Args:
        **kwargs: Expected {'0': condition, '1': true_value, '2': false_value}
        
    Returns:
        true_value if condition else false_value
    """
    try:
        if '0' not in kwargs or '1' not in kwargs or '2' not in kwargs:
            raise ValueError("Conditional requires three arguments: condition, true_value, false_value")
        
        condition = kwargs['0']
        true_value = kwargs['1']
        false_value = kwargs['2']
        
        return true_value if condition else false_value
    except Exception as e:
        raise ValueError(f"Conditional evaluation failed: {e}") from e
```

## Complex Example: Recursive Operations

For operations that need to work with the VoxLogicA system recursively or need access to the WorkPlan, you may need to implement more complex logic. However, most primitives should be simple, pure functions.

### Fibonacci Sequence Operator (`fibonacci.py`)

```python
"""
Fibonacci primitive for VoxLogicA-2

Computes the nth Fibonacci number using an iterative algorithm.
"""

def execute(**kwargs):
    """
    Execute fibonacci computation
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': n} where n is the position in fibonacci sequence
        
    Returns:
        The nth Fibonacci number
        
    Raises:
        ValueError: If arguments are invalid or missing
    """
    try:
        # Get the first argument (the position n)
        if '0' not in kwargs:
            raise ValueError("Fibonacci requires one argument: the position n")
        
        n = kwargs['0']
        
        # Convert to int if possible
        if isinstance(n, float) and n.is_integer():
            n = int(n)
        
        if not isinstance(n, int):
            raise ValueError("Fibonacci input must be an integer")
        if n < 0:
            raise ValueError("Fibonacci input must be non-negative")
        
        if n <= 1:
            return n
        
        # Iterative computation for efficiency
        a, b = 0, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        
        return b
    except Exception as e:
        raise ValueError(f"Fibonacci computation failed: {e}") from e
```

**Note**: VoxLogicA passes arguments to operators as keyword arguments with numeric string keys (`'0'`, `'1'`, etc.). Your `execute` function should use `**kwargs` and extract arguments using these numeric keys.

## Testing New Operators

### 1. Unit Testing

Create unit tests for your operator in the `tests/` directory:

```python
def test_fibonacci_operator():
    """Test the fibonacci operator directly"""
    from voxlogica.primitives.fibonacci import execute
    
    assert execute(0) == 0
    assert execute(1) == 1
    assert execute(5) == 5
    assert execute(10) == 55
```

### 2. Integration Testing

Create VoxLogicA programs (`.imgql` files) that use your operator:

```plaintext
// fibonacci_test.imgql
let n = 10
let result = fibonacci n
save "fibonacci_result.txt" result
```

### 3. Testing with VoxLogicA CLI

```bash
cd /Users/vincenzo/data/local/repos/VoxLogicA-2
./run-tests.sh fibonacci_test.imgql
```

## Advanced Features

### Type System Integration

VoxLogicA-2 includes a type system for buffer allocation optimization. Your operators automatically work with this system, but you can consider:

- **Input validation**: Ensure your operators handle the expected data types
- **Return type consistency**: Return consistent types for buffer allocation efficiency
- **Memory efficiency**: For large data operations, consider memory usage patterns

### Buffer Allocation Considerations

The buffer allocation system will automatically optimize memory usage for your operators. For optimal performance:

- **Avoid in-place modifications**: Return new values rather than modifying inputs
- **Consistent types**: Operations that return the same type can share buffers more efficiently
- **Memory patterns**: Consider how your operator fits into computation chains

## Dynamic Loading System

The `PrimitivesLoader` class automatically:

1. Converts operator names to module names (e.g., `fibonacci_sequence` → `fibonacci_sequence.py`)
2. Imports the module from `voxlogica.primitives.<module_name>`
3. Looks for the `execute` function
4. Caches loaded operators for performance
5. Provides error messages for missing operators

## Debugging New Operators

### Enable Debug Logging

```python
from voxlogica.error_msg import Logger
Logger.set_debug(True)
```

### Common Issues

1. **ImportError**: Check module name and file location
2. **AttributeError**: Ensure your module has an `execute` function
3. **TypeError**: Verify function signature matches expected arguments
4. **ValueError**: Check input validation and error handling

### Testing Operator Loading

```python
from voxlogica.execution import PrimitivesLoader

loader = PrimitivesLoader()
operator = loader.load_primitive("your_operator_name")
if operator:
    result = operator(arg1, arg2)
else:
    print("Failed to load operator")
```

## Best Practices

1. **Keep it simple**: Operators should be pure functions when possible
2. **Validate inputs**: Always check argument types and values
3. **Handle errors gracefully**: Provide meaningful error messages
4. **Document thoroughly**: Include docstrings and comments
5. **Test extensively**: Create both unit and integration tests
6. **Consider performance**: Optimize for the expected use cases
7. **Follow naming conventions**: Use consistent naming across the system

## Integration with VoxLogicA Language

Once implemented, your operator can be used in VoxLogicA programs:

```plaintext
// Using custom operators
let input1 = 42
let input2 = "test"
let result = your_custom_operator input1 input2
print "output.txt" result
```

The parser and reducer will automatically create DAG operations that reference your primitive, and the execution engine will load and execute it when needed.

## Memory and Performance Considerations

### Buffer Allocation

The buffer allocation system works transparently with your operators:
- Operations of the same type can share memory buffers
- The system optimizes memory usage across the entire computation DAG
- Your operators don't need to manage memory directly

### Performance Tips

1. **Avoid global state**: Keep operators stateless for better optimization
2. **Use efficient algorithms**: Consider computational complexity
3. **Memory usage**: Be aware of temporary object creation
4. **Caching**: The system handles memoization at the DAG level

## Extension Points

For advanced use cases, you can extend the operator system:

1. **Custom type systems**: Implement type-specific operators
2. **External library integration**: Wrap existing libraries as operators
3. **Parallel operators**: For operations that can benefit from parallelism
4. **Stateful operators**: For operations that need to maintain state (advanced)

## Conclusion

Implementing new operators in VoxLogicA-2 is straightforward:
1. Create a Python module in the `primitives/` directory
2. Implement an `execute` function with proper error handling
3. Test the operator both as a unit and integrated with VoxLogicA
4. Use the operator in VoxLogicA programs

The dynamic loading system handles all the integration details, allowing you to focus on implementing the core operation logic.