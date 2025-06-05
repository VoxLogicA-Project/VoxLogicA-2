# Static Buffer Reuse and Memory Planning for DAG Execution

This directory documents algorithms, design decisions, and implementation notes for static memory planning and buffer reuse in the execution of DAGs (Directed Acyclic Graphs) of computational tasks, with a focus on Python and scientific computing workflows (e.g., VoxLogicA, PyTorch, Dask).

## Purpose

- To enable efficient memory usage by preallocating and reusing buffers for task outputs, based on static analysis of the DAG.
- To provide a reference for implementing lifetime analysis, type/shape matching, and static scheduling for memory reuse.
- To serve as a foundation for future integration with parallel schedulers (e.g., Dask) and deep learning frameworks (e.g., PyTorch).

## Problem Statement

Given:
- A directed acyclic graph (DAG) representing computational tasks, where each node produces an output.
- A set of output nodes (marked as final outputs).
- For each node, the type and shape of its output are known and fixed.
- No information about execution times or dynamic scheduling is assumed.

Goal:
- Compute a mapping from DAG nodes to buffer IDs, where each buffer ID represents a unique memory allocation.
- If two nodes are mapped to the same buffer ID, they will write their outputs to the same memory location (buffer reuse).
- Buffer reuse is only allowed if the lifetimes of the outputs do not overlap (i.e., the output of one node is no longer needed before the other node writes to the buffer), and the type/shape are compatible.
- The mapping should be unambiguous: for every node, specify exactly which buffer ID it writes to.

This mapping enables static preallocation and efficient reuse of memory buffers during DAG execution, minimizing memory usage while ensuring correctness.

## Formal Input Specification

The input to the buffer assignment algorithm consists of:
- The DAG structure: nodes and directed edges representing dependencies.
- For each node, its output type (including shape and dimension).
- A compatibility relation `compatible(A, B)`, which returns true if a value of type A can be safely written to a buffer allocated for type B. This function may be the identity (i.e., only exact matches allowed), or more permissive depending on the application.

The algorithm must use this information to compute a valid and efficient mapping from nodes to buffer IDs, as described above.

## Contents

- `overview.md`: High-level overview and motivation
- `algorithms.md`: Lifetime analysis and buffer assignment algorithms
- `integration.md`: Notes on integrating with Dask, PyTorch, and other frameworks
- `references.md`: Related work and further reading
