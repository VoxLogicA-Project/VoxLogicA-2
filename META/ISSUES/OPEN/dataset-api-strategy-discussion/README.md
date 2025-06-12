# Dataset API Strategy: Dynamic Compilation After Dataset Loading

## Status: OPEN

## Issue

Determine the optimal strategy for dataset operation compilation in VoxLogicA-2, focusing on when and how to compile functions `f(x)` in `map(f, dataset)` operations.

## Created

2025-06-12

## Core Challenge: Function Compilation Timing

In VoxLogicA dataset operations like `map(f, dataset)`, we face a fundamental timing challenge:

**VoxLogicA Pattern**:
```voxlogica
let transform(x) = complex_computation(x)
let dataset = load_dataset("data.zarr") 
let result = map(transform, dataset)
```

**The Challenge**: 
- `transform(x)` contains VoxLogicA operations that need to be compiled to a Dask DAG
- We can't compile `f(x)` until we know what `x` actually is (specific dataset elements)
- Lazy evaluation means dataset loading happens after map operation compilation
- Each `f(x)` application needs its own complete DAG for proper memoization

## Solution Approaches Overview

Option 3 has been selected.

### Option 1: Per-Element Function Compilation

**Concept**: Compile `f(x)` as a complete DAG for each specific dataset element `x` during execution.

**Implementation**:
```python
def map_per_element(function_def, dataset):
    def apply_to_element(element):
        # Compile complete function call: f(element)
        function_call_dag = compile_function_call(function_def, element)
        return execute_workplan(function_call_dag)
    
    return dataset.map(apply_to_element)
```

**Trade-offs**: Simple implementation, but compilation overhead scales with dataset size.

### Option 2: Function Template Compilation

**Concept**: Compile function to a "template" DAG that can be instantiated with concrete arguments.

**Trade-offs**: Better performance but requires complex template system and dataset type knowledge.

### Option 3: Dynamic Compilation After Dataset Loading ⭐

**Concept**: Defer all function compilation until after the dataset has been loaded, then compile `f(x)` for each element `x`.

This is our **recommended approach** due to its flexibility and perfect integration with Dask's lazy evaluation model.

## Option 3: Dynamic Compilation with Dask's `@delayed` Decorator

### Core Concept

The key insight is to use Dask's `@delayed` decorator to create **nested lazy evaluation**, enabling us to:

1. **Defer compilation** until after dataset loading
2. **Compile `f(x)` for each element** `x` with full knowledge of actual dataset contents  
3. **Dynamically add tasks** to the Dask graph during execution

### How Dask's `@delayed` Enables This Pattern

Dask's `@delayed` decorator supports **nested lazy evaluation** - delayed functions can return results from other delayed functions:

```python
@delayed
def dynamic_map_compilation(function_def, dataset):
    """This function executes AFTER dataset loading"""
    
    # Now we have the actual dataset - compile f(x) for each element x
    def compile_and_apply(element):
        # Compile complete function call: f(element) as DAG
        function_call_dag = compile_function_call(function_def, element)
        return execute_workplan(function_call_dag)
    
    # Apply to dataset - creates new delayed tasks dynamically
    return dataset.map(compile_and_apply)
```

### Dependency Chain Architecture

The `@delayed` decorator creates a dependency chain:

```
Dataset Loading (delayed) → Function Compilation (delayed) → Execution
```

**Key Properties**:
- **No execution until `.compute()`**: Entire chain is lazy
- **Automatic dependency resolution**: Dask handles the ordering
- **Dynamic task creation**: New tasks added during execution
- **Nested delayed objects**: Delayed functions returning other delayed objects

### Implementation Example

```python
class DatasetMapPrimitive:
    def execute(self, function_name: str, dataset_operation_id: str, **kwargs):
        # Get dataset task from existing graph
        dataset_task = self.delayed_graph[dataset_operation_id]
        function_def = self.get_function_definition(function_name)
        
        @delayed
        def compile_after_loading(dataset):
            """Executes after dataset is loaded"""
            
            # Compile f(x) for each dataset element x
            def compile_and_apply(element):
                function_call_dag = compile_function_call(function_def, element)
                return execute_workplan(function_call_dag)
            
            return dataset.map(compile_and_apply)
        
        # Create new graph node with dependency on dataset loading
        return compile_after_loading(dataset_task)
```

### Perfect Integration with VoxLogicA-2's Architecture

VoxLogicA-2 already uses this pattern in `execution.py`:

```python
def _compile_pure_operations_to_dask(self, dependencies):
    """Current architecture: dynamic graph construction"""
    for op_id in topo_order:
        operation = self.pure_operations[op_id]
        
        # Dynamically add nodes based on workplan structure
        self.delayed_graph[op_id] = delayed(self._execute_pure_operation)(
            operation, op_id, *dep_delayed
        )
```

### Advantages of Option 3

**✅ Maximum Flexibility**: No need to know dataset structure at VoxLogicA compile time  
**✅ Full Memoization**: Each `f(x)` gets complete SHA256 hash for deduplication  
**✅ Interactive Development**: Perfect for REPL-style development and stateless APIs  
**✅ Dask Integration**: Leverages Dask's strengths in lazy evaluation and dynamic graphs  
**✅ Incremental Computation**: Reuse existing computations across sessions

**Trade-offs**: Compilation happens during execution rather than at VoxLogicA compile time, but this enables much greater flexibility.

## Interactive Execution Support

Option 3 enables powerful interactive development patterns:

### REPL-Style Development
```python
# Interactive session - each line creates new graph nodes dynamically
>>> dataset = load_dataset("data.zarr")          # Delayed task
>>> v1 = map(lambda x: x * 2, dataset)          # Delayed task  
>>> v2 = map(lambda x: x + 1, v1)               # Delayed task, reuses v1
>>> result = v2.compute()                       # Execute only when needed
```

### Stateless API Support
```python
# API can compile new code dynamically without maintaining state
@delayed
def compile_user_code(code_string, dataset):
    # Parse and compile user code AFTER dataset is loaded
    function_def = parse_voxlogica_code(code_string)
    
    # Compile f(x) for each dataset element x
    def compile_and_apply(element):
        function_call_dag = compile_function_call(function_def, element)
        return execute_workplan(function_call_dag)
    
    return dataset.map(compile_and_apply)
```

### Incremental Computation
```python
# Reuse existing computations across interactive sessions
base_dataset = load_dataset("data.zarr")           # Computed once

# Multiple experiments reuse the same base dataset
experiment_1 = map(function_a, base_dataset)       # New computation
experiment_2 = map(function_b, base_dataset)       # Reuses dataset
experiment_3 = map(function_c, experiment_1)       # Builds on experiment_1
```
