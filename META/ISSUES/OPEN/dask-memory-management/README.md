# Dask Memory Management Issue

**Created:** 6 luglio 2025  
**Status:** Open  
**Priority:** Medium  
**Type:** Performance/Memory

## Problem Description

VoxLogicA shows Dask memory warnings during execution of workplans containing `dask_map` operations:

```
[    1696ms] Unmanaged memory use is high. This may indicate a memory leak or the memory may not be released to the OS; see https://distributed.dask.org/en/latest/worker-memory.html#memory-not-released-back-to-the-os for more information. -- Unmanaged memory: 1.31 GiB -- Worker memory limit: 1.86 GiB
```

This occurs because `dask_map` operations produce Dask bag results that:
1. Cannot be serialized/persisted to disk storage ("Cannot ensure persistence for non-serializable result")
2. Are stored only in memory cache
3. Accumulate in memory without being released

## Root Cause

The issue stems from the execution model where `dask_map` operations are treated as special cases that need pre-execution outside the normal Dask delayed graph. While this approach works functionally, it leads to memory management problems:

1. **Non-serializable Results**: Dask bags cannot be pickled and stored in the SQLite storage backend
2. **Memory-only Storage**: Results stay in memory cache throughout execution
3. **No Cleanup**: Memory cache has no automatic cleanup mechanism for large non-serializable objects

## Current Workaround

The current implementation successfully executes `dask_map` operations by:
- Pre-executing them before the main Dask computation phase
- Storing results in memory cache only
- Allowing dependent operations to access results from memory

This ensures correctness but at the cost of increased memory usage.

## Potential Solutions

### Option 1: Lazy Dask Bag Evaluation
- Modify `dask_map` to return lazy Dask bag references instead of computed results
- Defer computation until the bag is actually consumed (e.g., by `.compute()`)
- This would reduce memory footprint but requires careful coordination with the execution model

### Option 2: Streaming/Chunked Processing
- Process Dask bags in chunks rather than loading entire results into memory
- Implement a streaming interface for operations that consume Dask bags
- More complex but would handle larger datasets

### Option 3: Temporary File Storage
- Serialize Dask bag results to temporary files using custom formats
- Clean up temporary files after execution completion
- Balances memory usage with disk I/O overhead

### Option 4: Memory Cache Limits
- Implement memory usage monitoring in the storage backend
- Automatically evict large non-serializable objects when memory pressure is high
- Simple but may impact performance if objects need to be recomputed

## Recommended Approach

**Phase 1** (Quick Fix): Implement memory cache limits with LRU eviction
- Add memory monitoring to storage backend
- Evict large objects when memory usage exceeds threshold
- Minimal code changes, immediate benefit

**Phase 2** (Long-term): Lazy Dask bag evaluation
- Redesign `dask_map` to work with lazy evaluation
- Integration with Dask's built-in memory management
- Better resource utilization and scalability

## Test Cases

- `test_simpleitk.imgql`: Complex nested for-loops with large medical images
- `test_dedup.imgql`: Simple deduplication scenario
- Performance regression tests to ensure no functionality loss

## Links

- Dask memory management documentation: https://distributed.dask.org/en/latest/worker-memory.html
- Related code: `implementation/python/voxlogica/execution.py` (ExecutionSession class)
- Test files: `test_simpleitk.imgql`, `test_dedup.imgql`
