### UNIFIED EXECUTION MODEL: Remove Artificial Dask vs Normal Distinction

**Date:** 7 luglio 2025
**Type:** Architecture Refactoring
**Priority:** High
**Component:** Execution Engine, Operation System

#### Current Problem

The VoxLogicA execution system artificially distinguishes between "normal" operations and "dask" operations, creating unnecessary complexity:

1. **Artificial categorization** in execution engine:
   ```python
   special_handling_operators = {'dask_map'}
   ```

2. **Separate execution paths**:
   - `_execute_dask_map_operations()` for "special" operations
   - Normal operation execution for everything else

3. **Pre-computation during graph construction**:
   - Closures execute immediately when called
   - Results computed before Dask tasks run
   - Breaks pure lazy evaluation

4. **Complex closure binding**:
   - Variable binding happens during graph construction
   - Same operation IDs generated for different iteration values
   - Incorrect memoization behavior

#### Proposed Unified Model

**Core Principle:** Every operation is just an operation - no special cases.

**Execution Flow:**
1. **All operations** are handled by the same execution mechanism
2. **Executing operation = launching Dask task**
3. **Dask tasks can dynamically add operations** to the workplan during execution
4. **Workplan acts as lazy "ready queue"** - operations execute when dependencies ready
5. **No pre-computation** - pure lazy evaluation

#### Example: `for i in range(1,100) do impure(i)`

**Current Broken Behavior:**
- Creates `dask_map` operation with closure
- Closure executes during graph construction
- Same operation ID for all iterations
- Pre-computed results put into Dask bag
- Result: `[random_value, random_value, ...]` (all same value)

**Desired Unified Behavior:**
1. Create `range(1,100)` operation
2. Create `for_loop` operation (depends on range)
3. Execute `range(1,100)` (Dask task) → produces `[1,2,3,...,99]`
4. Execute `for_loop` (Dask task) → **dynamically adds** 99 operations:
   - `impure(1)`, `impure(2)`, ..., `impure(99)`
5. Execute each `impure(i)` (Dask tasks) when ready
6. Result: `[1, 2, 3, ..., 99]` (correct values)

#### Implementation Tasks

##### Phase 1: Remove Special Handling
- [ ] Remove `special_handling_operators = {'dask_map'}` from execution engine
- [ ] Remove `_execute_dask_map_operations()` method
- [ ] Remove `_categorize_operations()` special case logic
- [ ] Make all operations go through unified execution path

##### Phase 2: Dynamic Workplan Expansion  
- [ ] Allow operations to add new operations to workplan during execution
- [ ] Implement thread-safe workplan modification from Dask tasks
- [ ] Add operation dependency tracking for dynamically added operations

##### Phase 3: Unified For-Loop Implementation
- [ ] Replace `dask_map` primitive with `for_loop` primitive  
- [ ] `for_loop` operation that dynamically expands during execution
- [ ] Remove closure pre-execution - defer until operation execution

##### Phase 4: Remove Closure Pre-Computation
- [ ] Modify `ClosureValue.__call__()` to not execute immediately
- [ ] Defer variable binding until actual operation execution
- [ ] Ensure unique operation IDs for different variable bindings

#### Expected Benefits

1. **Unified execution model** - no artificial distinctions
2. **Pure lazy evaluation** - no pre-computation
3. **Correct variable binding** - unique operation IDs per iteration
4. **Dynamic workplan** - operations added as needed
5. **Simplified codebase** - remove complex special case logic
6. **Better parallelism** - true Dask-based execution

#### Test Cases

- [ ] `test_impure_debug.imgql` - should produce `[1,2,3,...,99]` not `[X,X,X,...]`
- [ ] `test_simpleitk_small.imgql` - should work with proper variable binding
- [ ] Loop-invariant expressions should still be optimized via caching
- [ ] Loop-variant expressions should produce different results per iteration

#### Files to Modify

- `implementation/python/voxlogica/execution.py` - Remove special handling
- `implementation/python/voxlogica/reducer.py` - Fix closure pre-computation  
- `implementation/python/voxlogica/primitives/default/dask_map.py` - Replace with for_loop
- Create new `implementation/python/voxlogica/primitives/default/for_loop.py`

#### Success Criteria

- [ ] No distinction between operation types in execution engine
- [ ] All operations execute via unified Dask task mechanism
- [ ] Dynamic workplan expansion works correctly
- [ ] For loops produce correct per-iteration results
- [ ] No pre-computation during graph construction
- [ ] Workplan serves as dynamic ready queue
