# Issue: Goals as Special Dask Nodes

## Status
**OPEN** - Implementation in progress

## Description

VoxLogica2 workplans have "goals" besides operations. In the execution compiler, these goals should become special nodes of the Dask graph that will "print" to terminal or "save" to disk the value of their inputs.

## Requirements

- Goals should be treated as special Dask nodes in the execution graph
- Goals should not affect the voxlogica2 reducer or workplan definition
- Only the execution engine should be modified
- Goal nodes should handle terminal printing and disk saving
- Goals should execute after their dependency operations complete

## Current Implementation Issues

1. Goals are currently just operation IDs in the workplan, not special operations
2. The execution engine doesn't know the goal type (print vs save) from the operation ID alone
3. Goal execution is separate from the Dask graph, happening after computation

## Proposed Solution

1. Enhance the execution engine to create special goal operations that wrap regular operations
2. Integrate goal operations into the Dask delayed graph
3. Goal operations execute as Dask tasks that consume the results of their dependencies
4. Support both print (terminal output) and save (disk storage) goal types

## Files to Modify

- `implementation/python/voxlogica/execution.py` - Main execution engine modifications

## Implementation Plan

1. ✅ Create goal operation wrapper class
2. ✅ Enhance ExecutionSession to detect goal types from workplan context
3. ✅ Integrate goal operations into Dask delayed graph 
4. ✅ Implement goal execution as Dask tasks
5. ✅ Update goal operation handling (print/save)

## Created
2025-06-09

## Last Updated  
2025-06-09
