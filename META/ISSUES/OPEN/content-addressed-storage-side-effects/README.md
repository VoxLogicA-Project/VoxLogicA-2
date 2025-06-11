# Issue: Content-Addressed Storage Caching Side-Effect Operations

## Date
2025-06-11

## Status
**OPEN** - Deferred

## Priority
Medium - Affects user workflow but has workaround

## Description
The content-addressed storage system incorrectly caches side-effect operations like `WriteImage`, preventing files from being saved on subsequent executions. When a `WriteImage` operation is executed, its result is cached, and on subsequent runs the operation is skipped with "found in storage, skipping" even though the file write should be re-executed.

## Problem Statement
Side-effect operations (operations that perform I/O or modify external state) should not be cached the same way as pure computational operations. The current system treats all operations uniformly, leading to:

1. **File writes being skipped**: `WriteImage` operations show "found in storage, skipping"
2. **No output files generated**: Expected `.nii.gz` files are not created on subsequent runs
3. **Misleading success messages**: Execution reports success but no side-effects occur

## Evidence
```bash
# First run - works correctly
./voxlogica run --execute test_sitk.imgql
# Creates: chris_t1_thresholded_unqualified.nii.gz

# Second run - skips file write
./voxlogica run --execute test_sitk.imgql
# Output: "Operation 07976b0c... found in storage, skipping"
# No file created, but reports success
```

## Current Workaround
Manual cache clearing before each run:
```bash
rm -f ~/.voxlogica/storage.db && ./voxlogica run --execute test_sitk.imgql
```

## Technical Analysis
The issue is in the content-addressed storage design where:
- Pure operations (mathematical calculations) should be cached
- Side-effect operations (file I/O, printing) should NOT be cached
- Current system treats all operations uniformly

## Impact
- **User Experience**: Confusing behavior where operations appear successful but produce no output
- **Testing**: Requires manual cache clearing between test runs
- **Workflows**: Breaks idempotent execution expectations

## Proposed Solution Categories
1. **Operation Classification**: Distinguish pure vs side-effect operations
2. **Selective Caching**: Only cache pure computational operations
3. **Cache Keys**: Use different caching strategies for different operation types
4. **Goal Handling**: Treat `WriteImage` as execution goals rather than cached operations

## Related Components
- `voxlogica/execution.py` - Content-addressed storage logic
- `voxlogica/storage.py` - Storage backend implementation
- `voxlogica/primitives/simpleitk/` - SimpleITK WriteImage primitive

## Files Affected
- Test files: `test_sitk.imgql`, `test_sitk_simple_names.imgql`
- Storage: `~/.voxlogica/storage.db`

## References
- Related to distributed execution engine caching design
- Connects to purity requirements in DAG execution semantics

## Resolution Plan
This issue is **deferred** as it requires architectural changes to the storage system to distinguish between pure and side-effect operations. The current workaround (manual cache clearing) is acceptable for development and testing.
