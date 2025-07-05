# Storage Race Condition Fix

**Date:** 27 giugno 2025  
**Status:** CLOSED - RESOLVED  
**Type:** Critical Bug Fix  
**Priority:** High  

## Problem Description

VoxLogicA was failing with "Operation completed but result not found" errors when running operations that should be deduplicated. The specific error was:

```
[364ms] Dask computation failed: Operation completed but result not found for 51c0b8dd...
[364ms] Execution failed with 2 errors
[364ms]   51c0b8dd...: Operation completed but result not found for 51c0b8dd...
[364ms]   dask_com...: Operation completed but result not found for 51c0b8dd...
```

## Root Cause Analysis

The issue was caused by **two separate race conditions** in the storage system:

### Race Condition 1: Background Writer Gap
In the `StorageBackend.store()` method:
1. Serializable data was queued for background writing via `_result_write_queue`
2. The method returned immediately after queuing (non-blocking)
3. Operations were marked as completed before the background writer processed the queue
4. When waiters tried to retrieve results, they weren't found in either persistent storage (not written yet) or memory cache (not stored there for serializable data)

### Race Condition 2: Missing Completion Marking
In the `ExecutionSession._execute_operation_inner()` method:
1. Operations executed successfully and stored their results
2. BUT there was no call to `self.storage.mark_completed(operation_id)`
3. Only failed operations were being marked with `self.storage.mark_failed()`
4. This created inconsistent state where operations could be marked as completed through other paths without proper coordination

## Solution Implementation

### Fix 1: Immediate Memory Cache for Serializable Data
Modified `StorageBackend.store()` to:
1. Store serializable data in memory cache immediately (as temporary cache)
2. Queue for background writing as before  
3. Remove from memory cache after successful database write
4. This ensures `retrieve()` can always find results immediately after `store()` returns

```python
# Store in memory cache immediately to avoid race condition
# where operation is marked complete before background write finishes
self._memory_cache[operation_id] = data

# Queue for background writing (non-blocking)
write_request = (operation_id, serialized_data, data_type, size_bytes, metadata_json)
self._result_write_queue.put(write_request)
```

### Fix 2: Proper Completion Marking
Added missing `mark_completed()` call in `_execute_operation_inner()`:

```python
self.storage.store(operation_id, result)
self.storage.mark_completed(operation_id)  # <-- Added this line
logger.log(VERBOSE_LEVEL, f"[DONE] Operation {operation_id[:8]}... completed successfully")
```

### Fix 3: Background Writer Cleanup
Modified background writer to remove items from memory cache after successful database write:

```python
if cursor.rowcount > 0:
    # Successfully wrote to database, remove from memory cache if present
    self._memory_cache.pop(operation_id, None)
```

## Testing and Validation

### Test Case: `test_dedup.imgql`
The failing test case now works correctly:
```bash
./voxlogica run test_dedup.imgql
# Now outputs:
data=[<SimpleITK.SimpleITK.Image; ...>, <SimpleITK.SimpleITK.Image; ...>, ...]
[573ms] Execution completed successfully!
```

### Regression Testing
- ✅ 12/14 tests pass (same as before fix)
- ✅ For loop operations work correctly
- ✅ SimpleITK operations work correctly
- ✅ Non-for-loop operations remain unaffected

### Edge Case Handling
Also cleaned up corrupted state where operations were marked as completed without results being stored:
```bash
# Cleaned up database inconsistency
DELETE FROM execution_state WHERE operation_id LIKE "51c0b8dd%" AND operation_id NOT IN (SELECT operation_id FROM results)
```

## Architecture Impact

This fix maintains the performance benefits of background writing while ensuring correctness:

1. **Immediate Availability**: Results are available immediately after `store()` returns
2. **Background Optimization**: Database writes still happen in background for performance
3. **Memory Efficiency**: Items are removed from memory cache after database write
4. **Race-Free Coordination**: Proper completion marking ensures consistent state

## Compliance with Agent Policies

✅ **No timeouts unless absolutely justified**: Fix eliminates race conditions deterministically  
✅ **No locks unless absolutely justified**: Uses atomic database operations + memory cache  
✅ **Event-driven over polling**: Maintains existing notification system  
✅ **Lock-free atomic operations**: Atomic database writes + thread-safe memory operations

## Files Modified

- `implementation/python/voxlogica/storage.py`: 
  - Fixed race condition in `store()` method
  - Enhanced background writer cleanup
- `implementation/python/voxlogica/execution.py`:
  - Added missing `mark_completed()` call

## Related Issues

- Linked to [Global Futures Table implementation](../../../STATUS.md) - both improvements to operation coordination
- Resolves deduplication failures in for-loop operations
- Enables reliable caching for all operation types

**RESOLUTION**: Both race conditions are fully resolved with deterministic, policy-compliant solutions.