# Dataset API Strategy: Dynamic Compilation After Dataset Loading

## Status: IMPLEMENTATION READY

## Issue

Determine the optimal strategy for dataset operation compilation in VoxLogicA-2, focusing on when and how to compile functions `f(x)` in `map(f, dataset)` operations.

## Created & Resolved

2025-06-12

## Solution

**Strategy**: Dynamic VoxLogicA function compilation with SHA256 CBA IDs and Dask delayed execution

**Implementation Plan**: See `FINAL-IMPLEMENTATION-PLAN.md`

## Key Approach

- **Dynamic compilation**: VoxLogicA functions compiled per dataset element using element's CBA ID
- **Dask delayed integration**: Nested lazy evaluation with `@delayed` decorator  
- **Content addressing**: SHA256 CBA IDs maintain VoxLogicA-2's execution model
- **Interactive execution**: REPL-style development with incremental computation

## Next Steps

Implement according to `FINAL-IMPLEMENTATION-PLAN.md` - the single, comprehensive implementation guide.
