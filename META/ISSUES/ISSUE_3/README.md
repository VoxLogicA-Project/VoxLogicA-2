# Task: CPU-demanding test for reducer using function declarations for combinatorial explosion

## Summary

Create a test that causes combinatorial explosion in the workflow graph by using function declarations instead of constants. This is similar to the fibonacci chain task (Task 2) but using function declarations instead of constant declarations, which will cause the Workflow (DAG) size to grow combinatorially with respect to the imgql size.

## Issue

- GitHub Issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/3

## Status

- OPEN. Planning implementation for a test using function declarations that create combinatorial explosion in the DAG.

## Implementation Plan

1. Design and implement a sequence of imgql function declarations up to depth 20 (initially).
2. Use function declarations instead of constant declarations, with the aim to make the Workflow (DAG) size increase combinatorially.
3. Integrate the test into both Python and F# test runners.
4. Run the test and verify it causes combinatorial explosion in the workflow.
5. Save the DAG to a file to visualize the explosion.
6. Update documentation and traceability in META and GitHub.

## Traceability

- Task file cross-referenced with GitHub issue #3.
- Feature branch will follow the format: feature/3-function-explosion-test
