# Issue 8: DAG Equivalence Test Failure - Number Formatting Inconsistencies

## Problem

The DAG equivalence test that compares JSON output between Python and F# implementations is failing on test case 4. The issue is that the same program produces different operation ordering in the DAG between the two implementations.

## Reproducing the Issue

Test case 4 of the DAG equivalence test fails with the following imgql program:

```
let pi = 3.14
let area(r) = pi * r * r
let a = area(2)
print "area" a
```

This produces different JSON outputs:

- Python: `[{"arguments":[],"operator":3.14},{"arguments":[],"operator":2}...]`
- F#: `[{"arguments":[],"operator":2},{"arguments":[],"operator":3.14}...]`

The operations appear in different orders, causing the normalized JSON comparison to fail.

## Root Cause

The issue appears to be related to how numbers are formatted and how operations are ordered during DAG construction in the reducer. The F# and Python implementations are creating operations in different orders when processing the same program.

## Current Fix Status

- Branch: `fix/8-fsharp-number-formatting`
- Current changes attempt to standardize number formatting between implementations
- RFC 8785 JSON canonicalization (JCS) is being used via the `jcs` Python library for proper normalization

## Acceptance Criteria

- All DAG equivalence tests pass
- JSON output normalization uses RFC 8785 standard (JCS)
- Both implementations produce semantically equivalent DAGs for the same program
- Operation ordering differences are properly handled by normalization

## GitHub Issue Reference

This issue is tracked in GitHub issue: https://github.com/voxlogica-project/VoxLogicA-2/issues/8

## Detailed Analysis

### Why the Issue Remains Unsolved

The current fix attempts to address JSON number formatting differences, but the root cause is deeper. The fundamental issue is **operation ordering during DAG construction**, not JSON normalization.

**What we discovered:**

1. **RFC 8785 JSON normalization is already correct**: The test properly uses JCS (JSON Canonicalization Scheme) via the `jcs` library
2. **The real problem is structural**: Both implementations create semantically equivalent DAGs but with different operation IDs and argument references
3. **Example from failing test case 4**:

   ```
   let pi = 3.14
   let area(r) = pi * r * r
   let a = area(2)
   print "area" a
   ```

   Python creates operations: `[3.14, 2.0, *, *]` with arguments `[[],[],[1,1],[0,2]]`
   F# creates operations: `[2, 3.14, *, *]` with arguments `[[],[],[0,0],[1,2]]`

**Why normalization can't fix this:**

- Even with perfect JSON canonicalization, the operations reference different IDs
- Python's operation 0 is `3.14`, F#'s operation 0 is `2`
- This creates fundamentally different argument arrays that can't be normalized to match

**Root cause in the reducers:**

- Python and F# implementations traverse the AST or process function calls in different orders
- When `area(2)` expands to `pi * 2 * 2`, the implementations encounter constants in different sequences
- This leads to different operation creation orders and different DAG structures

### Required Solution

The fix needs to ensure **deterministic operation ordering** in both implementations:

1. **Option A**: Standardize AST traversal order (left-to-right, depth-first, etc.)
2. **Option B**: Implement semantic equivalence checking instead of structural matching
3. **Option C**: Sort operations by semantic content before JSON comparison
4. **Option D**: Use content-based operation IDs instead of creation-order IDs

### Current Status

- Branch `fix/8-fsharp-number-formatting` contains attempts at number formatting fixes
- RFC JSON normalization (JCS) is correctly implemented and working as intended
- The DAG equivalence test still fails on test case 4
- **The fundamental operation ordering issue remains unresolved**

## Status

- **BLOCKED**. The issue requires architectural changes to ensure deterministic operation creation order in both Python and F# implementations. Current number formatting changes do not address the root cause of different operation ordering during DAG construction.
