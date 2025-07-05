# 2025-01-05-operation-dependency-resolution-fix

## Issue: Non-Serializable Operation Cross-Process Coordination Error - RESOLVED ✅

### Problem
The `test_simpleitk.imgql` test was failing with "Operation produces non-serializable results and is not available in this process" error when executing nested for-loops. The issue occurred when the second `dask_map` operation tried to use the result of the first `dask_map` operation as a dependency.

### Root Cause Identified
The issue was in the dependency resolution and execution order for non-serializable operations:

1. **Incomplete Dependency Graph**: The `_build_dependency_graph()` method only included dependencies for `pure_operations`, but non-serializable operations were excluded from `pure_operations` by design.

2. **Missing Execution Order**: When `_execute_non_serializable_operations()` was called with the incomplete dependency graph, it couldn't properly establish execution order for dependencies between non-serializable operations.

3. **Cross-Process Coordination Attempt**: The second `dask_map` operation tried to wait for the first operation through cross-process mechanisms (`_wait_for_result`), but non-serializable results can't be shared across processes.

### Error Details
```
Operation cec70f49... produces non-serializable results and is not available in this process. Non-serializable operations must be computed within the same process.
```

This error occurred when the execution logic fell back to `_wait_for_result()` for dependency resolution, which detected that the dependency was a non-serializable operation and correctly rejected cross-process waiting.

### Solution Implemented
Implemented proper dependency handling for non-serializable operations by:

1. **Created dedicated dependency graph builder** for non-serializable operations
2. **Fixed execution order** through proper topological sorting 
3. **Enhanced dependency resolution** within the non-serializable execution context

### Code Changes

#### 1. Added `_build_non_serializable_dependency_graph` method
```python
def _build_non_serializable_dependency_graph(self, non_serializable_operations: Dict[NodeId, Operation]) -> Dict[NodeId, Set[NodeId]]:
    """
    Build dependency graph for non-serializable operations.
    
    Creates a mapping from each non-serializable operation to its dependencies,
    including dependencies on other non-serializable operations and any workplan nodes.
    """
    dependencies: Dict[NodeId, Set[NodeId]] = defaultdict(set)
    for op_id, operation in non_serializable_operations.items():
        for arg_name, dep_id in operation.arguments.items():
            # Include dependencies on other non-serializable operations
            # and any operation that exists in the workplan
            if dep_id in non_serializable_operations or dep_id in self.workplan.nodes:
                # Only add as dependency if it's another operation (not a constant)
                if dep_id in non_serializable_operations or (dep_id in self.workplan.nodes and isinstance(self.workplan.nodes[dep_id], Operation)):
                    dependencies[op_id].add(dep_id)
    return dict(dependencies)
```

#### 2. Modified `_compile_pure_operations_to_dask` to use proper dependency graph
```python
# Execute non-serializable operations directly before Dask computation
# This ensures they're available for any serializable operations that depend on them
if non_serializable_operations:
    logger.log(VERBOSE_LEVEL, f"Pre-executing {len(non_serializable_operations)} non-serializable operations")
    # Build dependency graph for non-serializable operations
    ns_dependencies = self._build_non_serializable_dependency_graph(non_serializable_operations)
    self._execute_non_serializable_operations(non_serializable_operations, ns_dependencies)
```

#### 3. Enhanced dependency resolution in `_execute_non_serializable_operations`
```python
elif isinstance(dep_node, Operation):
    # Need to compute this dependency first
    # If it's also a non-serializable operation, execute directly without cross-process coordination
    logger.log(VERBOSE_LEVEL, f"Computing dependency {dep_id[:8]}... for non-serializable operation {op_id[:8]}...")
    if dep_id in non_serializable_operations:
        # Execute non-serializable dependency directly without coordination
        dep_result = self._execute_operation_inner(dep_node, dep_id, [])
    else:
        # Use normal execution for serializable dependencies
        dep_result = self._execute_pure_operation(dep_node, dep_id)
    dependency_results.append(dep_result)
```

### Test Results
After the fix:

**`test_simpleitk.imgql`**: ✅ **WORKING**
- Nested for-loops execute successfully in correct order
- Both `dask_map` operations execute without cross-process coordination issues
- Produces expected output: `data2=[(0.0, 101.0), (0.0, 101.0), ...]` (499 tuples)

**`test_simple_for.imgql`**: ✅ **WORKING** (No regression)
- Simple for-loop continues to work correctly
- Output: `simple_result=[10.0, 11.0, 12.0, 13.0, 14.0]`

**`test_dedup.imgql`**: ✅ **WORKING** (No regression)
- Deduplication test with SimpleITK images works correctly
- Output: `data=[<SimpleITK.SimpleITK.Image; proxy of ...>, ...]`

### Impact
- ✅ **Fixed nested non-serializable operations** - Proper execution order and dependency resolution for non-serializable operations within the same process
- ✅ **Maintains process isolation** - Non-serializable operations correctly isolated to single process without cross-process coordination attempts
- ✅ **No regressions** - All existing tests continue to pass
- ✅ **Proper topological sorting** - Non-serializable operations executed in dependency order

### Technical Details
The fix ensures that non-serializable operations (like `dask_map`) are:
1. **Properly ordered** using their own dependency graph
2. **Executed within the same process** without cross-process coordination
3. **Resolved correctly** when used as dependencies by other non-serializable operations

This solution maintains the design principle that non-serializable results cannot be shared across processes while enabling complex nested operations within a single process context.

### Status: RESOLVED ✅
Date: 2025-01-05
Duration: ~2 hours

The VoxLogicA-2 system now correctly handles nested for-loops with non-serializable operations, resolving the cross-process coordination issue that was preventing `test_simpleitk.imgql` from working properly.
