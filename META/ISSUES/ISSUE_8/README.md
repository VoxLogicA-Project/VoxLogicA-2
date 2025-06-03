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

## Status

- IN PROGRESS. Working on standardizing number formatting and ensuring proper JSON normalization in the equivalence tests.
