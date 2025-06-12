# Type System Development for VoxLogicA-2

## Status: OPEN

## Issue

VoxLogicA-2 currently lacks a formal type system. This is needed for:
1. Dataset operations and type compatibility
2. Buffer allocation type checking
3. Operation composition validation
4. Error prevention and debugging

## Created

2025-06-12

## Background

**Current State**: 
- No formal type system exists
- Buffer allocation uses placeholder `"basic_type"` for all operations
- Type compatibility is handled by simple equality check
- Dataset operations will require proper type hierarchy

**Need**: Formal type system to support:
- Dataset types (`Array[Float64]`, `DataFrame[Schema]`, etc.)
- Operation type checking and inference
- Memory allocation optimization
- Better error messages

## Scope

### Minimum Requirements
1. **Basic Type Hierarchy**: Numbers, strings, booleans, images
2. **Dataset Types**: Arrays, dataframes, collections with element types
3. **Type Compatibility**: Rules for operation composition
4. **Integration**: With existing buffer allocation and memoization

### Advanced Features (Future)
1. **Type Inference**: Automatic type detection from operations
2. **Generic Types**: Parameterized types for collections
3. **Shape Types**: Dimensional information for arrays/images
4. **Schema Types**: Structured data with field types

## Example Type System

```python
# Base types
Int64, Float64, String, Boolean, Image2D, Image3D

# Collection types  
Array[T], DataFrame[Schema], Dataset[T]

# Operations with type signatures
map: (T -> U, Dataset[T]) -> Dataset[U]
reduce: ((T, T) -> T, Dataset[T]) -> T
filter: (T -> Boolean, Dataset[T]) -> Dataset[T]
```

## Integration Points

### Buffer Allocation
```python
# Instead of placeholder types
type_assignment = lambda op_id: "basic_type"

# Proper type assignment
type_assignment = lambda op_id: infer_operation_type(op_id)
type_compatibility = lambda t1, t2: is_compatible(t1, t2)
```

### Dataset Operations
```python
# Type-aware dataset loading
load_dataset: URI -> Dataset[T]  # T inferred from metadata

# Type-checked operations
map_op = MapOp(function_type="Int64 -> Float64", 
               dataset_type="Dataset[Int64]")
result_type = "Dataset[Float64]"
```

## Implementation Strategy

### Phase 1: Basic Types
1. Define core type hierarchy
2. Update buffer allocation to use real types
3. Add type checking to existing primitives

### Phase 2: Dataset Types
1. Implement dataset type system
2. Add type inference for dataset operations
3. Integrate with Dask collection types

### Phase 3: Advanced Features
1. Generic types and type parameters
2. Schema types for structured data
3. Type inference engine

## Priority

**Medium-High** - Required for dataset operations but can be developed incrementally alongside dataset prototype.

## Related Issues

- Dataset API Strategy Discussion (`META/ISSUES/OPEN/dataset-api-strategy-discussion/`)
- Buffer allocation currently uses placeholder types
- Future dataset operations will require proper typing
