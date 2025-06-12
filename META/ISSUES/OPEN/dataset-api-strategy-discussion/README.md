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

## Dask Collection Type Selection for Dataset Operations

With Option 3 selected, we need to choose the appropriate Dask collection type for different dataset scenarios. Each collection type has different strengths for dataset operations.

### Dask Collection Types Analysis

#### 1. `dask.bag` - Unstructured Data Collections ⭐

**Best for**: General-purpose dataset operations, heterogeneous data, flexible schemas

```python
import dask.bag as db

# Load dataset as Dask bag
dataset = db.from_delayed([delayed(load_chunk)(chunk_id) for chunk_id in chunk_list])

@delayed
def dynamic_map_with_bag(function_def, dataset_bag):
    def compile_and_apply(element):
        # Compile f(x) for each element x
        function_call_dag = compile_function_call(function_def, element)
        return execute_workplan(function_call_dag)
    
    # Bag provides flexible map operations
    return dataset_bag.map(compile_and_apply)
```

**Advantages**:
- ✅ **Flexible element types**: Can handle any Python object
- ✅ **Simple API**: Straightforward map/filter/reduce operations
- ✅ **Heterogeneous data**: Different elements can have different structures
- ✅ **Dynamic partitioning**: Can repartition based on data characteristics

**Use cases**: Image collections, mixed data types, variable-size elements

#### 2. `dask.array` - Structured Numerical Data

**Best for**: Homogeneous numerical datasets, chunked arrays, mathematical operations

```python
import dask.array as da

# Load dataset as Dask array (e.g., medical images, tensor data)
dataset = da.from_delayed(
    delayed_chunks, 
    shape=(n_samples, height, width, channels),
    dtype=np.float32
)

@delayed
def dynamic_map_with_array(function_def, dataset_array):
    def compile_and_apply_blocks(block):
        # Process entire blocks for efficiency
        results = []
        for element in block:
            function_call_dag = compile_function_call(function_def, element)
            results.append(execute_workplan(function_call_dag))
        return np.array(results)
    
    # Array provides block-wise operations
    return dataset_array.map_blocks(compile_and_apply_blocks, dtype=...)
```

**Advantages**:
- ✅ **Efficient chunking**: Automatic data partitioning
- ✅ **NumPy compatibility**: Seamless integration with scientific libraries
- ✅ **Block-wise operations**: Process multiple elements together
- ✅ **Memory efficiency**: Lazy loading of large arrays

**Use cases**: Medical imaging datasets, satellite imagery, tensor operations

#### 3. `dask.dataframe` - Tabular Data

**Best for**: Structured tabular datasets with schema

```python
import dask.dataframe as dd

# Load dataset as Dask DataFrame
dataset = dd.from_delayed([delayed(load_partition)(p) for p in partitions])

@delayed
def dynamic_map_with_dataframe(function_def, dataset_df):
    def compile_and_apply_row(row):
        function_call_dag = compile_function_call(function_def, row)
        return execute_workplan(function_call_dag)
    
    # DataFrame provides row-wise or column-wise operations
    return dataset_df.apply(compile_and_apply_row, axis=1, meta=...)
```

**Advantages**:
- ✅ **Schema awareness**: Column types and structure
- ✅ **SQL-like operations**: Familiar tabular operations
- ✅ **Pandas compatibility**: Seamless integration
- ✅ **Efficient filters**: Pre-filtering before map operations

**Use cases**: Metadata tables, structured annotations, feature datasets

### Recommended Strategy: `dask.bag` as Primary Choice

For VoxLogicA-2 dataset operations, **`dask.bag` is the recommended primary choice**:

#### Why `dask.bag` is Optimal for VoxLogicA-2:

1. **Flexibility with Unknown Data Types**:
   ```python
   # Can handle any dataset element type
   bag = db.from_sequence([
       {"image": image1, "metadata": meta1},  # Dict
       SimpleITKImage(path2),                 # Image object
       np.array([1, 2, 3]),                   # Array
       "string_data"                          # String
   ])
   ```

2. **Perfect for Dynamic Compilation**:
   ```python
   @delayed
   def adaptive_map_compilation(function_def, dataset_bag):
       def compile_for_element(element):
           # Element type determined at runtime
           function_call_dag = compile_function_call(function_def, element)
           return execute_workplan(function_call_dag)
       
       return dataset_bag.map(compile_for_element)
   ```

3. **Easy Partitioning Control**:
   ```python
   # Repartition based on dataset characteristics
   dataset_bag = dataset_bag.repartition(npartitions=optimal_partition_count)
   ```

4. **Integration with Option 3**:
   ```python
   class DatasetMapPrimitive:
       def execute(self, function_name: str, dataset_operation_id: str, **kwargs):
           dataset_bag = self.delayed_graph[dataset_operation_id]
           function_def = self.get_function_definition(function_name)
           
           @delayed
           def compile_after_loading(bag):
               def compile_and_apply(element):
                   function_call_dag = compile_function_call(function_def, element)
                   return execute_workplan(function_call_dag)
               
               return bag.map(compile_and_apply)
           
           return compile_after_loading(dataset_bag)
   ```

### Hybrid Approach: Type-Aware Collection Selection

For advanced scenarios, implement type-aware collection selection:

```python
@delayed
def smart_collection_selection(dataset_spec, data_loader):
    """Choose collection type based on dataset characteristics"""
    
    # Load sample to determine optimal collection type
    sample = data_loader.peek()
    
    if dataset_spec.is_homogeneous_arrays():
        # Use dask.array for numerical data
        return load_as_dask_array(dataset_spec, data_loader)
    elif dataset_spec.is_tabular():
        # Use dask.dataframe for structured data
        return load_as_dask_dataframe(dataset_spec, data_loader)
    else:
        # Default to dask.bag for flexibility
        return load_as_dask_bag(dataset_spec, data_loader)

@delayed
def dynamic_map_with_type_awareness(function_def, dataset_spec):
    dataset = smart_collection_selection(dataset_spec, data_loader)
    
    def compile_and_apply(element):
        function_call_dag = compile_function_call(function_def, element)
        return execute_workplan(function_call_dag)
    
    # Apply appropriate map operation based on collection type
    if hasattr(dataset, 'map_blocks'):  # dask.array
        return dataset.map_blocks(compile_and_apply, dtype=...)
    elif hasattr(dataset, 'apply'):     # dask.dataframe
        return dataset.apply(compile_and_apply, axis=1, meta=...)
    else:                               # dask.bag
        return dataset.map(compile_and_apply)
```

### Performance Considerations

#### Memory Usage Patterns:

| Collection Type | Memory Efficiency | Chunking Strategy | Best Dataset Size |
|----------------|-------------------|-------------------|-------------------|
| `dask.bag` | Good (flexible) | Element-based | Any size |
| `dask.array` | Excellent (blocks) | Block-based | Large homogeneous |
| `dask.dataframe` | Good (partitions) | Row-based | Large tabular |

#### Parallelization Efficiency:

```python
# Bag: Element-level parallelization
dataset_bag.map(func).compute()  # Each element processed independently

# Array: Block-level parallelization  
dataset_array.map_blocks(func).compute()  # Blocks processed in parallel

# DataFrame: Partition-level parallelization
dataset_df.apply(func, axis=1).compute()  # Partitions processed in parallel
```

### Implementation Roadmap for Collection Types

#### Phase 1: `dask.bag` Implementation (Weeks 1-2)
- Basic dataset loading as Dask bags
- Element-wise map operations with dynamic compilation
- Simple partitioning strategies

#### Phase 2: Collection Type Detection (Weeks 3-4)
- Automatic detection of dataset characteristics
- Smart collection type selection
- Performance benchmarking across types

#### Phase 3: Specialized Optimizations (Weeks 5-6)
- Array-specific optimizations for numerical data
- DataFrame integration for structured data
- Hybrid collection strategies

#### Phase 4: Advanced Features (Weeks 7+)
- Custom chunking strategies
- Memory-aware collection selection
- Performance monitoring and optimization

### Next Steps for Collection Implementation

1. **Start with `dask.bag`**: Implement basic functionality using bags for maximum flexibility
2. **Add type detection**: Build dataset analysis to choose optimal collection type
3. **Benchmark performance**: Compare collection types with real VoxLogicA workloads
4. **Optimize based on usage**: Add specialized optimizations for common patterns

The `dask.bag` foundation provides the flexibility needed for Option 3's dynamic compilation while allowing future optimization with specialized collection types.
