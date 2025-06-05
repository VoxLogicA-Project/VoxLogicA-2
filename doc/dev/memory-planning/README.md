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

## Contents

- `overview.md`: High-level overview and motivation
- `algorithms.md`: Lifetime analysis and buffer assignment algorithms
- `integration.md`: Notes on integrating with Dask, PyTorch, and other frameworks
- `references.md`: Related work and further reading
