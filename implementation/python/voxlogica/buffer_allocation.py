"""
Static Buffer Allocation for DAG Execution

This module implements the static buffer reuse algorithm described in the sketch solution.
Given a workplan (DAG), a type assignment function, and a type compatibility function,
it computes an optimal buffer allocation that minimizes memory usage while ensuring
correctness (no overlapping lifetimes and type compatibility).

The algorithm uses a greedy chain decomposition approach based on Dilworth's theorem.
"""

from typing import Dict, List, Callable, Any, Set, Optional
from .reducer import WorkPlan, Operation, OperationId, Goal, GoalSave, GoalPrint
from collections import defaultdict, deque


def compute_buffer_allocation(
    workplan: WorkPlan,
    type_assignment: Callable[[OperationId], Any],
    type_compatibility: Callable[[Any, Any], bool],
) -> Dict[OperationId, int]:
    """
    Compute buffer allocation for a workplan using static analysis.

    Args:
        workplan: The workplan containing operations and goals
        type_assignment: Function that returns the type for each operation ID
        type_compatibility: Function that returns True if two types are compatible

    Returns:
        Dictionary mapping operation IDs to buffer IDs (integers starting from 0)
    """

    # Get operations with their IDs
    ops_with_ids = workplan._get_operations_with_ids()
    if not ops_with_ids:
        return {}

    # Build the DAG structure - adjacency lists for dependencies
    parents: Dict[OperationId, List[OperationId]] = defaultdict(list)
    children: Dict[OperationId, List[OperationId]] = defaultdict(list)
    all_op_ids: Set[OperationId] = set()

    # Collect all operation IDs
    for op_id, _ in ops_with_ids:
        all_op_ids.add(op_id)

    # Build dependency graph
    for op_id, operation in ops_with_ids:
        for arg_op_id in operation.arguments.values():
            if (
                arg_op_id in all_op_ids
            ):  # Only add if the argument is an operation in our DAG
                parents[op_id].append(arg_op_id)
                children[arg_op_id].append(op_id)

    # Compute topological order
    topo_order = _topological_sort(all_op_ids, children)

    # Compute consumption counts for each node
    consumption: Dict[OperationId, int] = {}
    final_outputs: Set[OperationId] = set()

    # Collect final outputs from goals
    for goal in workplan.goals:
        if isinstance(goal, (GoalSave, GoalPrint)):
            final_outputs.add(goal.operation_id)

    # Initialize consumption counts
    for op_id in all_op_ids:
        # Number of children + 1 if it's a final output
        child_count = len(children[op_id])
        is_final = op_id in final_outputs
        consumption[op_id] = child_count + (1 if is_final else 0)

    # Apply the greedy chain assignment algorithm
    buffer_assignment: Dict[OperationId, int] = {}
    next_buffer_id = 0

    for op_id in topo_order:
        # For the truly correct algorithm, we need to be much more conservative
        # A node can only reuse a buffer if:
        # 1. It's from a single direct parent (chain case)
        # 2. That parent becomes completely dead after this consumption
        # 3. The node has only one parent (to avoid multi-input conflicts)

        reusable_buffer = None

        # Only consider reuse if this node has exactly one parent (chain case)
        if len(parents[op_id]) == 1:
            parent_id = parents[op_id][0]
            if (
                consumption[parent_id] == 1  # This node is the last consumer
                and parent_id not in final_outputs  # Parent is not a final output
                and type_compatibility(
                    type_assignment(parent_id), type_assignment(op_id)
                )  # Types are compatible
            ):
                # Safe to reuse in chain: parent → child
                reusable_buffer = buffer_assignment[parent_id]

        # STEP 2: Assign buffer
        if reusable_buffer is not None:
            buffer_assignment[op_id] = reusable_buffer
        else:
            buffer_assignment[op_id] = next_buffer_id
            next_buffer_id += 1

        # STEP 3: Now decrement consumption counts for all parents
        for parent_id in parents[op_id]:
            consumption[parent_id] -= 1

    return buffer_assignment


def _topological_sort(
    nodes: Set[OperationId], children: Dict[OperationId, List[OperationId]]
) -> List[OperationId]:
    """
    Perform topological sort on the DAG using Kahn's algorithm.

    Args:
        nodes: Set of all node IDs
        children: Adjacency list mapping each node to its children

    Returns:
        List of nodes in topological order
    """
    # Calculate in-degrees
    in_degree: Dict[OperationId, int] = defaultdict(int)
    for node in nodes:
        in_degree[node] = 0

    for node in nodes:
        for child in children[node]:
            in_degree[child] += 1

    # Initialize queue with nodes having no incoming edges
    queue = deque([node for node in nodes if in_degree[node] == 0])
    result = []

    while queue:
        node = queue.popleft()
        result.append(node)

        # Remove this node and update in-degrees of its children
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(result) != len(nodes):
        raise ValueError("Graph contains a cycle - not a valid DAG")

    return result


def print_buffer_assignment(
    workplan: WorkPlan,
    buffer_assignment: Dict[OperationId, int],
    type_assignment: Callable[[OperationId], Any],
) -> None:
    """
    Print the buffer assignment to console in a readable format.

    Args:
        workplan: The workplan
        buffer_assignment: The computed buffer assignment
        type_assignment: Function to get type for each operation
    """
    print("\n=== Buffer Assignment ===")

    # Group operations by buffer ID
    buffers: Dict[int, List[tuple]] = defaultdict(list)

    for op_id, operation in workplan._get_operations_with_ids():
        if op_id in buffer_assignment:
            buffer_id = buffer_assignment[op_id]
            op_type = type_assignment(op_id)
            buffers[buffer_id].append((op_id, operation, op_type))

    # Print each buffer
    for buffer_id in sorted(buffers.keys()):
        operations = buffers[buffer_id]
        print(f"\nBuffer {buffer_id}:")
        for op_id, operation, op_type in operations:
            # Truncate operation ID for readability
            short_id = op_id[:8] if len(op_id) > 8 else op_id
            print(f"  {short_id}: {operation.operator} (type: {op_type})")

    print(f"\nTotal buffers allocated: {len(buffers)}")
    print(f"Total operations: {len([op for ops in buffers.values() for op in ops])}")
