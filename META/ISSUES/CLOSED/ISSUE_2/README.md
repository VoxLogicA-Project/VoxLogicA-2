# Task: CPU-demanding test for reducer (imgql Fibonacci-like chain)

## Summary

Create a test that is CPU-demanding for the reducer. This involves creating a series of imgql function declarations that use previous declarations in a Fibonacci-like (albeit non-recursive) fashion. The goal is to stress-test the reducer with a deep chain of dependent function calls.

## Issue

- GitHub Issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/2

## Status

- COMPLETE. Implementation finished, all tests pass, DAG saved, and documentation updated. Merged in commit SHA: c6a9837e7e235983143931e5c4a44ad2cbb1fb7b.

## Steps Completed

1. Designed and implemented a sequence of imgql function declarations up to f100 (depth 100).
2. Integrated the test into both Python and F# test runners.
3. Ran the test and verified it is CPU-demanding for the reducer (100 tasks).
4. Saved the DAG to /tmp/dag.txt using the Python implementation.
5. Updated documentation and traceability in META and GitHub.

## Traceability

- Task file and GitHub issue cross-referenced.
- Feature branch: feature/2-cpu-demanding-reducer-test
- Merge commit SHA: c6a9837e7e235983143931e5c4a44ad2cbb1fb7b
