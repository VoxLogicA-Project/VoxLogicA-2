# Issue: Static Buffer Reuse and Memory Planning for DAG Execution

## Problem Statement

I have a computational workflow represented as a directed acyclic graph (DAG). Each node in the DAG represents a task that produces an output. For every node, the type and shape (including dimension) of its output are known and fixed. Some nodes are marked as final outputs.

### Input:
- The DAG structure: nodes and directed edges representing dependencies.
- For each node, its output type (shape, dimension, etc.).
- A compatibility relation `compatible(A, B)`, which returns true if a value of type A can be safely written to a buffer allocated for type B. This function may be the identity (only exact matches allowed), or more permissive.

### Constraints:
- The DAG may be executed in any order that respects dependencies; parallel execution is possible.
- Each node’s output is read only by its dependent nodes, and only during their execution.
- Buffer reuse is allowed: two nodes can be assigned the same buffer if and only if their output lifetimes do not overlap (i.e., the buffer is not needed for reading after it is overwritten), and their type/shape are compatible.

### Goal:
- Compute a mapping from DAG nodes to buffer IDs, where each buffer ID represents a unique memory allocation.
- If two nodes are mapped to the same buffer ID, they will write their outputs to the same memory location (buffer reuse).
- The mapping should minimize the total number of buffers allocated, while ensuring correctness (no overlapping lifetimes, type/shape compatibility), regardless of execution order (as long as dependencies are respected).

### Deliverable:
- An efficient algorithm (with pseudocode or implementation guidance) that, given the DAG structure, output types/shapes, the compatibility relation, and final output nodes, computes the optimal (or near-optimal) buffer assignment for static preallocation and reuse, valid for any valid execution order.
- A correctness proof that the algorithm always produces a valid buffer assignment (i.e., no overlapping lifetimes for reused buffers, and type/shape compatibility is always respected).

