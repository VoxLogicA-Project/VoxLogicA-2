from typing import Dict, Callable, Any, Set, List, Tuple
from collections import defaultdict, deque
from voxlogica.reducer import WorkPlan, NodeId


def print_buffer_assignment(
    workplan: WorkPlan,
    buffer_assignment: Dict[NodeId, int],
    type_assignment_func: Callable[[NodeId], Any],
) -> None:
    """
    Print a formatted console output of the buffer assignment.

    Args:
        workplan: The WorkPlan containing operations
        buffer_assignment: Dictionary mapping NodeId to buffer_id
        type_assignment_func: Function that returns the type for each operation ID
    """
    print("\n=== Buffer Assignment ===\n")

    # Group operations by buffer ID
    buffer_to_ops: Dict[int, List[NodeId]] = defaultdict(list)
    for op_id, buffer_id in buffer_assignment.items():
        buffer_to_ops[buffer_id].append(op_id)

    # Print each buffer and its assigned operations
    for buffer_id in sorted(buffer_to_ops.keys()):
        print(f"Buffer {buffer_id}:")
        for op_id in buffer_to_ops[buffer_id]:
            operation = workplan.operations[op_id]
            op_type = type_assignment_func(op_id)
            print(f"  {op_id[:8]}: {operation.operator} (type: {op_type})")
        print()

    print(f"Total buffers allocated: {len(buffer_to_ops)}")
    print(f"Total operations: {len(workplan.operations)}")
    print()


def _build_dependency_graph(
    workplan: WorkPlan,
) -> Tuple[Dict[NodeId, Set[NodeId]], Dict[NodeId, Set[NodeId]]]:
    """
    Build dependency graph from WorkPlan.

    Returns:
        Tuple of (dependencies, dependents) where:
        - dependencies[op] = set of operations that op depends on (parents)
        - dependents[op] = set of operations that depend on op (children)
    """
    dependencies: Dict[NodeId, Set[NodeId]] = defaultdict(set)
    dependents: Dict[NodeId, Set[NodeId]] = defaultdict(set)

    for operation_id, operation in workplan.operations.items():
        for arg_name, dependency_id in operation.arguments.items():
            if dependency_id in workplan.operations:
                dependencies[operation_id].add(dependency_id)
                dependents[dependency_id].add(operation_id)

    return dict(dependencies), dict(dependents)


def _topological_sort(
    workplan: WorkPlan, dependencies: Dict[NodeId, Set[NodeId]]
) -> List[NodeId]:
    """
    Perform topological sort using Kahn's algorithm.

    Returns:
        List of operation_ids in topological order.
    """
    # Calculate in-degrees
    in_degree: Dict[NodeId, int] = defaultdict(int)
    for operation_id in workplan.operations:
        in_degree[operation_id] = len(dependencies.get(operation_id, set()))

    # Find nodes with no incoming edges
    queue = deque([op_id for op_id in workplan.operations if in_degree[op_id] == 0])
    result: List[NodeId] = []

    while queue:
        current = queue.popleft()
        result.append(current)

        # Reduce in-degree of dependents
        for op_id, deps in dependencies.items():
            if current in deps:
                in_degree[op_id] -= 1
                if in_degree[op_id] == 0:
                    queue.append(op_id)

    if len(result) != len(workplan.operations):
        raise ValueError("Cycle detected in WorkPlan")

    return result


def _compute_operation_lifetimes(
    workplan: WorkPlan,
    dependents: Dict[NodeId, Set[NodeId]],
    topo_order: List[NodeId],
) -> Dict[NodeId, Tuple[int, int]]:
    """
    Compute the lifetime of each operation as (start_time, end_time) where
    start_time = topological position of the operation,
    end_time = maximum position among its direct dependents (or start_time if none).

    Returns:
        Dictionary mapping NodeId to (start_time, end_time) tuple.
    """
    position: Dict[NodeId, int] = {op_id: i for i, op_id in enumerate(topo_order)}
    lifetimes: Dict[NodeId, Tuple[int, int]] = {}

    for op_id in workplan.operations:
        start_time = position[op_id]
        deps = dependents.get(op_id, set())
        if deps:
            end_time = max(position[dep] for dep in deps)
        else:
            end_time = start_time
        lifetimes[op_id] = (start_time, end_time)

    return lifetimes


def compute_buffer_allocation(
    workplan: WorkPlan,
    type_assignment: Callable[[NodeId], Any],
    type_compatibility: Callable[[Any, Any], bool],
) -> Dict[NodeId, int]:
    """
    Compute buffer allocation for a workplan using static analysis.

    Args:
        workplan: The workplan containing operations
        type_assignment: Function mapping operation IDs to types
        type_compatibility: Function that returns True if two types are compatible (symmetric)

    Returns:
        Dictionary mapping operation IDs to buffer IDs (integers starting from 0)
    """
    # Build dependency graph
    dependencies, dependents = _build_dependency_graph(workplan)
    # Topological ordering
    topo_order = _topological_sort(workplan, dependencies)
    # Compute lifetimes of operations
    lifetimes = _compute_operation_lifetimes(workplan, dependents, topo_order)

    buffer_allocation: Dict[NodeId, int] = {}
    buffer_to_operations: Dict[int, Set[NodeId]] = {}
    next_buffer_id = 0

    # Process operations in reverse topological order
    for op_id in reversed(topo_order):
        op_type = type_assignment(op_id)
        # Compute descendants of op_id (all reachable children)
        visited: Set[NodeId] = set()
        queue = deque([op_id])
        visited.add(op_id)
        while queue:
            curr = queue.popleft()
            for child in dependents.get(curr, set()):
                if child not in visited:
                    visited.add(child)
                    queue.append(child)
        descendants = visited

        assigned = False
        for buffer_id, ops in buffer_to_operations.items():
            # Type compatibility check for all ops in buffer
            if all(type_compatibility(type_assignment(u), op_type) for u in ops):
                conflict = False
                for u in ops:
                    # Parent-child conflict: direct child of op_id
                    if u in dependents.get(op_id, set()):
                        conflict = True
                        break
                    # Concurrency conflict: no ancestor-descendant relationship
                    if u not in descendants:
                        conflict = True
                        break
                    # Lifetime overlap conflict
                    (start_v, end_v) = lifetimes[op_id]
                    (start_u, end_u) = lifetimes[u]
                    if not (end_u < start_v or end_v < start_u):
                        conflict = True
                        break
                if not conflict:
                    buffer_allocation[op_id] = buffer_id
                    buffer_to_operations[buffer_id].add(op_id)
                    assigned = True
                    break

        if not assigned:
            buffer_allocation[op_id] = next_buffer_id
            buffer_to_operations[next_buffer_id] = {op_id}
            next_buffer_id += 1

    return buffer_allocation


def allocate_buffers(
    workplan: WorkPlan, type_assignment: Dict[NodeId, Any]
) -> Dict[NodeId, int]:
    """
    Allocate buffers for operations in a WorkPlan with type-equality compatibility.

    Args:
        workplan: The WorkPlan containing operations
        type_assignment: Dictionary mapping NodeId to type

    Returns:
        Dictionary mapping NodeId to buffer IDs (integers starting from 0)
    """

    # Define compatibility as equality
    def type_compat(t1: Any, t2: Any) -> bool:
        return t1 == t2

    # Convert dict to function
    type_assign_func = lambda op: type_assignment[op]
    return compute_buffer_allocation(workplan, type_assign_func, type_compat)
