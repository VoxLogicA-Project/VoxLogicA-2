# Parser Operator-Function Confusion Issue

**Issue ID:** 2025-06-22-parser-operator-function-confusion  
**Priority:** High  
**Status:** ✅ **CLOSED**  
**Date Created:** 22 giugno 2025  
**Date Resolved:** 22 giugno 2025  
**Date Closed:** 22 giugno 2025

## Problem Statement

Recent parser changes broke the tokenization of function names that start with uppercase letters, causing `ReadImage` and similar functions to be incorrectly parsed as operators followed by unrecognized tokens.

## Error Behavior

```
[       0ms] VoxLogicA version: 2.0.0a2
[      15ms] Operation failed: Unexpected error: Unexpected token Token('__ANON_0', 'eadImage') at line 5, column 12.

I guess this is because of the confusion between prefix, infix operators, and functions. Maybe the easiest thing is to let operators start only with a symbol (and let functions and constants be free to use uppercase letters)
```

**Root Cause**: The `OPERATOR` regex pattern included uppercase letters `[A-Z]`, causing function names like `ReadImage` to be tokenized as:
1. `R` (matched as operator)
2. `eadImage` (unrecognized token)

## File Affected

`/Users/vincenzo/data/local/repos/VoxLogicA-2/test_sitk.imgql` line 5:
```
let img = ReadImage("tests/chris_t1.nii.gz")
```

## ✅ SOLUTION IMPLEMENTED

Modified the `OPERATOR` pattern in `implementation/python/voxlogica/parser.py` to exclude uppercase letters:

### Change Applied
```python
# Before (BROKEN):
OPERATOR: /(?!\/{2})[A-Z#;:_'|!$%&\/^=*\-+<>?@~\\]+/

# After (FIXED):
OPERATOR: /(?!\/{2})[#;:_'|!$%&\/^=*\-+<>?@~\\]+/
```

### Rationale
- **Operators** should only start with symbols (`+`, `-`, `*`, `/`, etc.)
- **Functions and constants** can freely use uppercase letters (`ReadImage`, `BinaryThreshold`, etc.)
- This clear separation prevents tokenization conflicts

## Testing Results

✅ **Fixed file execution**:
```bash
./voxlogica run test_sitk.imgql
# [925ms] Execution completed successfully!
```

✅ **Preserved operator functionality**:
```bash
./voxlogica run test_stdlib.imgql
# [477ms] sum=3.0 (using + operator)
```

✅ **General parser functionality**:
```bash
./voxlogica run test.imgql
# [561ms] Execution completed successfully!
```

## Impact Analysis

### Before Fix
- ❌ Function names starting with uppercase letters failed to parse
- ❌ `ReadImage`, `BinaryThreshold`, etc. caused parser errors
- ❌ Broke SimpleITK integration and other uppercase function names

### After Fix
- ✅ Function names with uppercase letters parse correctly
- ✅ Symbolic operators (`+`, `-`, etc.) continue working
- ✅ Clear separation between operators and identifiers
- ✅ Maintains backward compatibility for all existing functionality

## Related Components

- `implementation/python/voxlogica/parser.py` - Grammar definition and tokenization
- `test_sitk.imgql` - Test case demonstrating the issue
- `test_stdlib.imgql` - Verification that operators still work
- SimpleITK integration - Functions like `ReadImage`, `BinaryThreshold`

## Root Cause Analysis

This issue was introduced in commit `5852656` which modified the parser grammar structure. The enhancement added `function_name` and `variable_name` grammar rules that could match both `identifier` and `OPERATOR`, but the `OPERATOR` pattern was too broad, including uppercase letters that should be reserved for identifiers.

## Design Decision

**Chosen approach**: Restrict operators to symbol-only patterns
- **Operators**: Must start with symbols (`#`, `;`, `:`, `_`, `'`, `|`, `!`, `$`, `%`, `&`, `/`, `^`, `=`, `*`, `-`, `+`, `<`, `>`, `?`, `@`, `~`, `\\`)
- **Functions/Constants**: Can use any alphanumeric characters including uppercase letters

This approach provides clear separation and follows common programming language conventions where operators are symbolic and identifiers are alphanumeric.

## Success Criteria Met

- ✅ **Function names parse correctly**: `ReadImage`, `BinaryThreshold`, etc.
- ✅ **Operators still work**: `+`, `-`, `*`, `/`, etc.
- ✅ **No regression**: All existing test cases pass
- ✅ **Clear separation**: Unambiguous distinction between operators and identifiers

## Future Considerations

This fix establishes a clear tokenization boundary that should be maintained:
- New operators should use symbolic characters
- Function names should use alphanumeric characters
- This prevents future tokenization conflicts
