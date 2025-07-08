### Test Case: Verify Unified Execution Model Fix

This test demonstrates the unified execution model working correctly.

**Before Fix:**
```
IMPURE CALLED WITH: 31  (only once, random value)
data=[31, 31, 31, ..., 31]  (99 copies of same value)
```

**After Fix (Expected):**
```
IMPURE CALLED WITH: 1
IMPURE CALLED WITH: 2  
IMPURE CALLED WITH: 3
...
IMPURE CALLED WITH: 99
data=[1, 2, 3, ..., 99]  (correct sequence)
```

**Command to test:**
```bash
./voxlogica run test_impure_debug.imgql --debug
```
