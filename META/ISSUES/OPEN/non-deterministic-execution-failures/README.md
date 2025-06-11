# Issue: Non-Deterministic Execution Failures - SimpleITK Threshold Operation

## Date
2025-06-11

## Status
**OPEN** - Critical Investigation Required

## Priority
High - Affects reliability and production readiness

## Description
Identical VoxLogicA program executions produce different results when run multiple times with fresh storage. The same `test_sitk.imgql` file sometimes succeeds and sometimes fails with SimpleITK Threshold operation errors, despite no changes to the input file or program.

## Problem Statement
Running the exact same command with fresh storage produces inconsistent results:

### Successful Execution
```bash
rm -f ~/.voxlogica/storage.db && ./voxlogica run --execute test_sitk.imgql
# Result: "Execution completed successfully! Operations completed: 4"
# Output: "Image thresholded with unqualified names, value=100.0"
```

### Failed Execution (Same Command)
```bash
rm -f ~/.voxlogica/storage.db && ./voxlogica run --execute test_sitk.imgql
# Result: "Execution failed with 2 errors"
# Error: "Threshold failed: in method 'Threshold', argument 1 of type 'itk::simple::Image const &'"
```

## Evidence
Two consecutive runs of the identical command show completely different behavior:

**Run 1 (Success)**:
- Operations completed: 4
- Execution time: 0.14s  
- Output: "Image thresholded with unqualified names, value=100.0"

**Run 2 (Failure)**:
- Operation `3726fe96...` failed with Threshold error
- Dask computation failed
- 2 total errors reported

## Technical Analysis
The non-determinism suggests potential issues in:

1. **Concurrency/Threading**: Race conditions in Dask execution
2. **Data Marshaling**: Inconsistent data type conversion between VoxLogicA and SimpleITK
3. **Dynamic Loading**: SimpleITK primitive registration happening differently
4. **Memory State**: Stale references or improper cleanup between operations
5. **Input Validation**: Inconsistent argument resolution or type checking

## Key Error Details
- **Error Location**: SimpleITK Threshold operation (`3726fe96...`)
- **Error Type**: `argument 1 of type 'itk::simple::Image const &'`
- **Suggests**: Type mismatch or invalid image data being passed to Threshold

## Impact
- **Development**: Cannot reliably test SimpleITK workflows
- **Production**: Unacceptable failure rate for identical operations
- **User Trust**: Undermines confidence in the execution engine
- **Debugging**: Difficult to reproduce and investigate issues

## Investigation Areas
1. **Input Data Flow**: Trace how image data flows from ReadImage to Threshold
2. **Type Conversion**: Examine SimpleITK wrapper argument handling
3. **Concurrency Analysis**: Check for race conditions in Dask execution
4. **Primitive Loading**: Verify consistent SimpleITK function registration
5. **Memory Management**: Look for memory corruption or reference issues

## Test Case
**File**: `test_sitk.imgql`
**Command**: `rm -f ~/.voxlogica/storage.db && ./voxlogica run --execute test_sitk.imgql`
**Expected**: Consistent success or consistent failure
**Actual**: Random success/failure with no code changes

## Required Analysis
1. Run multiple executions with debug output to identify patterns
2. Examine SimpleITK primitive wrapper implementation
3. Investigate Dask delayed graph execution for threading issues
4. Check argument resolution and type marshaling between operations
5. Add logging to track data flow through the pipeline

## Related Components
- `voxlogica/execution.py` - Dask execution engine
- `voxlogica/primitives/simpleitk/` - SimpleITK wrappers
- `implementation/python/voxlogica/primitives/simpleitk/__init__.py` - Dynamic registration
- Threshold primitive wrapper and argument handling

## Urgency
**High** - This issue must be resolved before the system can be considered stable for production use. Non-deterministic execution is unacceptable for a computational platform.
