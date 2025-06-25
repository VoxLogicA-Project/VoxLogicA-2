# Issue: Add --no-cache Switch to VoxLogicA

**Status:** CLOSED - IMPLEMENTED  
**Date:** 25 giugno 2025  
**Type:** Feature Enhancement  

## Problem Statement

VoxLogicA uses content-addressed storage for memoization, which is excellent for performance but sometimes users need to force recomputation of all operations without reading or writing persistent cache. This is useful for:

1. Testing/debugging scenarios where you want to ensure all operations are freshly computed
2. Benchmarking actual computation time without cache benefits
3. Development scenarios where you want to verify operation implementations work correctly

## Solution Implemented

Added a `--no-cache` command-line option to the `run` command that:

1. Forces all operations to be recomputed from scratch
2. Uses a temporary in-memory SQLite database during execution
3. Allows goals to access computed results during execution
4. Discards all results when execution completes (no persistent storage)

## Technical Implementation

### Components Modified

1. **CLI Interface** (`implementation/python/voxlogica/main.py`)
   - Added `--no-cache` option to the run command
   - Passed option through the feature system

2. **Feature Handler** (`implementation/python/voxlogica/features.py`)
   - Added `no_cache` parameter to `handle_run()`
   - Implemented temporary ExecutionEngine replacement for no-cache mode
   - Proper engine restoration after execution

3. **Storage Backend** (`implementation/python/voxlogica/storage.py`)
   - Created `NoCacheStorageBackend` class inheriting from `StorageBackend`
   - Uses shared in-memory SQLite database (`file:no_cache_<uuid>?mode=memory&cache=shared`)
   - Provides full StorageBackend interface compatibility
   - No persistent disk storage

4. **Documentation** (`README.md`)
   - Added `--no-cache` option to CLI documentation

### Key Design Decisions

1. **In-Memory SQLite**: Uses shared in-memory SQLite instead of completely bypassing storage, because the execution system is fundamentally designed around storage memoization for goal operations.

2. **Temporary Engine Replacement**: Creates a custom ExecutionEngine with NoCacheStorageBackend temporarily, then restores the original engine to avoid affecting subsequent operations.

3. **Full Interface Compatibility**: NoCacheStorageBackend inherits from StorageBackend and uses the parent's methods with an in-memory database, ensuring complete compatibility.

## Usage

```bash
# Normal execution with caching
./voxlogica run test.imgql

# Force recomputation without cache
./voxlogica run test.imgql --no-cache

# Can be combined with other options
./voxlogica run test.imgql --no-cache --verbose --debug
```

## Testing Results

**With --no-cache (test.imgql):**
```
[START] Executing operation 23302bb6... (timewaste)
[DONE] Operation 23302bb6... completed successfully
[START] Executing operation 3c7a9bf0... (addition)  
[DONE] Operation 3c7a9bf0... completed successfully
Execution time: 0.30s
```

**Without --no-cache (test.imgql):**
```
Operation 23302bb6... found in storage, skipping
Operation 3c7a9bf0... found in storage, skipping
Execution time: 0.00s
```

The feature works correctly - operations are forced to recompute with `--no-cache` and use cached results without it.

## Files Modified

- `implementation/python/voxlogica/main.py` - Added CLI option
- `implementation/python/voxlogica/features.py` - Added feature logic
- `implementation/python/voxlogica/storage.py` - Added NoCacheStorageBackend
- `README.md` - Added documentation
- `test_no_cache.imgql` - Added test file

## Resolution

The `--no-cache` switch has been successfully implemented and tested. Users can now force VoxLogicA to recompute all operations without reading or writing persistent cache by using the `--no-cache` flag.
