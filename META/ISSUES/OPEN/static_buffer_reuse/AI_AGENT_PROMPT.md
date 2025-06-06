# Static Buffer Reuse Problem: AI Agent Prompt

## Problem Statement

You need to implement a **static buffer reuse algorithm** for computational graphs (directed acyclic graphs representing mathematical expressions). The goal is to minimize the number of memory buffers needed by reusing buffers when operands are no longer needed.

## Background & Theory

### Dilworth's Theorem Connection

This problem is related to **Dilworth's theorem** on partially ordered sets:

- In a DAG representing computation dependencies, we have a partial order where A ≺ B if A must be computed before B
- The minimum number of buffers needed equals the maximum number of nodes that must be "alive" simultaneously
- A node is "alive" from when it's computed until all its consumers have finished reading from it

### Literature Reference

The classical approach treats this as a **graph coloring problem** where:

- Each node needs a buffer (color)
- Two nodes can share a buffer if their "lifetimes" don't overlap
- Lifetime of node A: from when A executes until all nodes that depend on A have finished executing

## Critical Constraints

### 1. Read-Before-Write Constraint

**FUNDAMENTAL RULE**: A child node CANNOT reuse its parent's buffer because:

- The child must READ from the parent's buffer to get input values
- The child would WRITE its result to the same buffer
- This creates a read/write conflict - the child would overwrite data it still needs to read

### 2. Buffer Lifetime Rules

A buffer can only be reused when:

- The previous node's value is no longer needed by ANY consumer
- All consumers of that value have finished reading from it

### 3. Topological Ordering

- Process nodes in topological order (dependencies first)
- A node can only execute after all its parents have executed

## Current Broken Algorithm

The existing algorithm incorrectly allows immediate reuse:

```python
# WRONG - allows parent-child buffer sharing
for parent_id in parents[op_id]:
    if consumption[parent_id] == 1:  # Last consumer?
        buffer_assignment[op_id] = buffer_assignment[parent_id]  # WRONG!
        break
```

This fails because when `consumption[parent_id] == 1`, the current node IS that last consumer, meaning it still needs to read from the parent's buffer.

## Test Case That Exposes the Bug

Expression: `1.0 + 2.0`

Graph structure:

- Node "1.0" (no parents)
- Node "2.0" (no parents)
- Node "+" (parents: "1.0", "2.0")

**Wrong result**:

- "1.0" → buffer 0
- "2.0" → buffer 1
- "+" → buffer 0 (reuses "1.0"'s buffer) ❌

**Why it's wrong**: Node "+" needs to read from buffer 0 (where "1.0" stored its result) while writing its own result to buffer 0 - impossible!

**Correct result should be**:

- "1.0" → buffer 0
- "2.0" → buffer 1
- "+" → buffer 2 (new buffer, OR reuse after reading is complete)

## Required Algorithm Properties

### Input

- `workplan`: Computational graph with nodes and dependencies
- `type_assignment`: Maps node_id → data_type
- `compatibility_function`: Returns true if two types can share buffers

### Output

- `buffer_assignment`: Maps node_id → buffer_id
- Minimizes total number of buffers while respecting all constraints

### Correctness Requirements

1. **No read/write conflicts**: If node B reads from node A, they cannot share a buffer
2. **Type compatibility**: Only nodes with compatible types can share buffers
3. **Topological validity**: Respects execution order dependencies
4. **Optimality**: Uses minimum number of buffers possible under constraints

## Implementation Requirements

The algorithm should be implemented in Python with:

```python
def compute_buffer_allocation(workplan, type_assignment, compatibility_function):
    """
    Compute optimal buffer allocation for a computational graph.

    Args:
        workplan: Graph with nodes and dependencies
        type_assignment: Dict[node_id, type]
        compatibility_function: Callable[[type1, type2], bool]

    Returns:
        Dict[node_id, buffer_id]: Mapping from nodes to buffer IDs
    """
    pass
```

## Algorithm Approach Suggestions

Consider these approaches:

### 1. Lifetime-Based Approach

- Compute exact lifetime intervals for each node
- Use interval scheduling algorithm to assign buffers
- Lifetime of node A: [execution_time_A, max(execution_time of A's consumers)]

### 2. Graph Coloring Approach

- Build interference graph where edges connect nodes that cannot share buffers
- Apply graph coloring algorithm
- Two nodes interfere if their lifetimes overlap OR one reads from the other

### 3. Reference Counting with Delay

- Track reference counts but don't reuse buffers immediately
- Mark buffer as "available for reuse" only after ALL consumers finish
- Use delayed reuse queue

## Expected Behavior

For the test case `1.0 + 2.0`:

```
Processing node: 1.0
- Assign buffer 0
- consumption[1.0] = 1 (used by +)

Processing node: 2.0
- Assign buffer 1
- consumption[2.0] = 1 (used by +)

Processing node: +
- Reads from buffer 0 (1.0's value) and buffer 1 (2.0's value)
- consumption[1.0] -= 1 → 0 (now dead)
- consumption[2.0] -= 1 → 0 (now dead)
- Can now reuse buffer 0 OR buffer 1 (both are free)
- Assign buffer 0 or 1 to +
```

## Success Criteria

The algorithm succeeds if:

1. ✅ Produces valid buffer assignments (no conflicts)
2. ✅ Handles complex graphs with multiple levels
3. ✅ Respects type compatibility constraints
4. ✅ Minimizes buffer count optimally
5. ✅ Passes all test cases including the `1.0 + 2.0` example

## Context Integration

This algorithm will be integrated into a larger system that:

- Parses mathematical expressions into computational graphs
- Generates execution plans
- Exports results to DOT/JSON formats with buffer annotations
- Displays buffer assignments in a web UI

The implementation should be production-ready with proper error handling and documentation.
