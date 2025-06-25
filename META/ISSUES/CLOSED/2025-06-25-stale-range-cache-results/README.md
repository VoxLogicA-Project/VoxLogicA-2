# Issue: Stale Range Cache Results After Primitive Fix

**Date:** 25 giugno 2025  
**Status:** CLOSED  
**Priority:** High  

## Problem Description

After the range primitive was fixed in commit `395cc52` to support two-argument calls (`range(start, stop)`), users were still experiencing empty range results. The range implementation was correct, but cached results from before the fix were being returned instead of fresh computations.

## Symptoms

- `range(0,10)` returned `[]` instead of `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]`
- Debug output showed: `Operation 5666e3aa... found in storage, skipping`
- Using `--no-cache` flag produced correct results

## Root Cause

VoxLogicA's content-addressed storage system caches computation results based on operation signatures. When the range primitive was broken (before commit `395cc52`), empty results were cached. After the fix, the same operation signatures retrieved the old cached empty results instead of recomputing with the fixed implementation.

**Technical Details:**
- The operation hash for `range(0,10)` remained the same before and after the fix
- Storage system correctly retrieved cached result, but it was stale
- Content-addressed storage doesn't automatically invalidate results when primitive implementations change

## Solution Implemented

**Immediate Fix:** Cleared the storage cache to remove stale results:
```bash
rm -f ~/.voxlogica/storage.db
```

**Verification:**
- ✅ `range(0,10)` now returns `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]`
- ✅ `test_simpleitk.imgql` works correctly with range operations
- ✅ No need to use `--no-cache` flag

## Long-term Implications

This highlights an important characteristic of content-addressed storage systems:

### When Primitive Implementations Change

1. **Cache Invalidation is Manual**: Fixing a primitive doesn't automatically invalidate cached results
2. **Development vs Production**: In development, clearing cache may be necessary after fixes
3. **Versioning Consideration**: Future versions might need primitive version tracking

### Best Practices

1. **After Primitive Fixes**: Consider clearing storage cache if behavior changes
2. **Testing Strategy**: Use `--no-cache` to verify fixes work independently of cache
3. **Documentation**: Document when cache clearing is needed

## Files Involved

- `~/.voxlogica/storage.db` - Storage cache (removed)
- `implementation/python/voxlogica/primitives/default/range.py` - Fixed implementation (no changes needed)
- `test_simpleitk.imgql` - Test file that exposed the issue
- `test_range_debug.imgql` - Created for debugging

## Related Issues

- **Fixed in:** `395cc52 fixed range implementation` 
- **Cached results from:** Pre-fix broken range implementation
- **Storage behavior:** Working as designed, but cache invalidation needed

## Testing

**Before Fix:**
```bash
./voxlogica run test_range_debug.imgql
# result=[]
```

**After Cache Clear:**
```bash
./voxlogica run test_range_debug.imgql  
# result=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
```

**Impact:** Zero impact on functionality, purely a cache staleness issue resolved by clearing storage.
