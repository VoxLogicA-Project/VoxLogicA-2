# Task: CPU-demanding test for reducer (imgql Fibonacci-like chain)

## Summary

Create a test that is CPU-demanding for the reducer. This involves creating a series of imgql function declarations that use previous declarations in a Fibonacci-like (albeit non-recursive) fashion. The goal is to stress-test the reducer with a deep chain of dependent function calls.

## Issue

- GitHub Issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/2

## Status

- Task created and tracked in META and GitHub.
- Implementation started: Test file `tests/fibonacci_chain.imgql` created. (Now extended to depth 100/f100.)
- Integration and execution pending.

## Next Steps

1. Integrate the new test into the Python and F# test runners if possible.
2. Run the test and verify that it is CPU-demanding for the reducer.
3. Document the test and its results.
4. Update status and traceability in META and GitHub.

## Traceability

- Task file and GitHub issue cross-referenced.
