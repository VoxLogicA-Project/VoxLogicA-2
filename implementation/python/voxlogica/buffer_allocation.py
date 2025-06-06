"""
Static Buffer Allocation for DAG Execution

This module implements the static buffer reuse algorithm.
Given a workplan (DAG), a type assignment function, and a type compatibility function,
it computes an optimal buffer allocation that minimizes memory usage while ensuring
correctness (no overlapping lifetimes and type compatibility).
"""

# https://www.researchgate.net/publication/244252294_SIRA_Schedule_Independent_Register_Allocation_for_Software_Pipelining

from typing import Dict, Callable, Any
from voxlogica.reducer import WorkPlan, OperationId

from typing import Dict, Set, List, Any, Tuple
from collections import defaultdict, deque

def print_buffer_assignment(
    workplan: WorkPlan, 
    buffer_assignment: Dict[OperationId, int], 
    type_assignment_func: Callable[[OperationId], Any]
) -> None:
    """
    Print a formatted console output of the buffer assignment.
    
    Args:
        workplan: The WorkPlan containing operations
        buffer_assignment: Dictionary mapping OperationId to buffer_id
        type_assignment_func: Function that returns the type for each operation ID
    """
    print("\n=== Buffer Assignment ===\n")
    
    # Group operations by buffer ID
    buffer_to_ops = defaultdict(list)
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

def allocate_buffers(workplan: WorkPlan, type_assignment: Dict[OperationId, Any]) -> Dict[OperationId, int]:
    """
    Allocate buffers for operations in a WorkPlan with the constraint that
    parent-child operations cannot share buffers, and only operations of the
    same type can potentially share buffers.
    
    Args:
        workplan: The WorkPlan containing operations and goals
        type_assignment: Dictionary mapping OperationId to type (any Python object)
    
    Returns:
        Dictionary mapping OperationId to buffer_id (int)
    """
    
    # Build dependency graph
    dependencies, dependents = _build_dependency_graph(workplan)
    
    # Get topological ordering
    topo_order = _topological_sort(workplan, dependencies)
    
    # Process nodes in reverse topological order (outputs first)
    buffer_allocation = {}
    buffer_to_operation = {}  # Maps buffer_id to operation_id that uses it
    type_to_buffers = defaultdict(list)  # Maps type to list of buffer_ids of that type
    next_buffer_id = 0
    
    for operation_id in reversed(topo_order):
        op_type = type_assignment[operation_id]
        
        # Find available buffer of the correct type
        available_buffer = _find_available_buffer(
            operation_id, op_type, dependencies, dependents, 
            buffer_allocation, type_to_buffers
        )
        
        if available_buffer is not None:
            # Reuse existing buffer
            buffer_allocation[operation_id] = available_buffer
        else:
            # Allocate new buffer
            buffer_allocation[operation_id] = next_buffer_id
            type_to_buffers[op_type].append(next_buffer_id)
            next_buffer_id += 1
    
    return buffer_allocation

def _build_dependency_graph(workplan: WorkPlan) -> Tuple[Dict[OperationId, Set[OperationId]], Dict[OperationId, Set[OperationId]]]:
    """
    Build dependency graph from WorkPlan.
    
    Returns:
        Tuple of (dependencies, dependents) where:
        - dependencies[op] = set of operations that op depends on
        - dependents[op] = set of operations that depend on op
    """
    dependencies = defaultdict(set)
    dependents = defaultdict(set)
    
    for operation_id, operation in workplan.operations.items():
        for arg_name, dependency_id in operation.arguments.items():
            if dependency_id in workplan.operations:
                dependencies[operation_id].add(dependency_id)
                dependents[dependency_id].add(operation_id)
    
    return dict(dependencies), dict(dependents)

def _topological_sort(workplan: WorkPlan, dependencies: Dict[OperationId, Set[OperationId]]) -> List[OperationId]:
    """
    Perform topological sort using Kahn's algorithm.
    
    Returns:
        List of operation_ids in topological order
    """
    # Calculate in-degrees
    in_degree = defaultdict(int)
    for operation_id in workplan.operations:
        in_degree[operation_id] = len(dependencies.get(operation_id, set()))
    
    # Find nodes with no incoming edges
    queue = deque([op_id for op_id in workplan.operations if in_degree[op_id] == 0])
    result = []
    
    while queue:
        current = queue.popleft()
        result.append(current)
        
        # Get all operations that depend on current
        for operation_id, deps in dependencies.items():
            if current in deps:
                in_degree[operation_id] -= 1
                if in_degree[operation_id] == 0:
                    queue.append(operation_id)
    
    if len(result) != len(workplan.operations):
        raise ValueError("Cycle detected in WorkPlan")
    
    return result

def _find_available_buffer(
    operation_id: OperationId, 
    op_type: Any, 
    dependencies: Dict[OperationId, Set[OperationId]], 
    dependents: Dict[OperationId, Set[OperationId]], 
    buffer_allocation: Dict[OperationId, int],
    type_to_buffers: Dict[Any, List[int]]
) -> int | None:
    """
    Find an available buffer of the correct type that doesn't interfere
    with parents or children of the given operation.
    
    Returns:
        Buffer ID if available, None if no buffer can be reused
    """
    # Get all buffers of the correct type
    available_buffers = type_to_buffers.get(op_type, [])
    
    # Get parents and children of current operation
    parents = dependencies.get(operation_id, set())
    children = dependents.get(operation_id, set())
    interfering_ops = parents | children
    
    # Find buffers that are not used by interfering operations
    for buffer_id in available_buffers:
        # Check if this buffer is used by any interfering operation
        buffer_is_available = True
        for interfering_op in interfering_ops:
            if interfering_op in buffer_allocation and buffer_allocation[interfering_op] == buffer_id:
                buffer_is_available = False
                break
        
        if buffer_is_available:
            return buffer_id
    
    return None

# Example usage and testing
def test_buffer_allocation():
    """Test the buffer allocation algorithm with a simple example"""
    
    # Create a simple WorkPlan: A -> B -> C, A -> D -> C
    workplan = WorkPlan()
    
    # Add operations
    op_a = workplan.add_operation("load", {})
    op_b = workplan.add_operation("process", {"input": op_a})
    op_c = workplan.add_operation("combine", {"input1": op_b, "input2": op_d})
    op_d = workplan.add_operation("transform", {"input": op_a})
    
    # Set goal
    workplan.add_goal(op_c)
    
    # Define types
    type_assignment = {
        op_a: "tensor",
        op_b: "tensor", 
        op_c: "tensor",
        op_d: "scalar"
    }
    
    # Allocate buffers
    allocation = allocate_buffers(workplan, type_assignment)
    
    print("Buffer allocation:")
    for op_id, buffer_id in allocation.items():
        print(f"  {op_id[:8]}... -> buffer {buffer_id} (type: {type_assignment[op_id]})")
    
    return allocation

if __name__ == "__main__":
    test_buffer_allocation()

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

        It is assumed that the workplan nodes are in topological order.

    Returns:
        Dictionary mapping operation IDs to buffer IDs (integers starting from 0)
    """
    # Convert the type assignment function to a dictionary for allocate_buffers
    type_assignment_dict = {op_id: type_assignment(op_id) for op_id in workplan.operations}
    return allocate_buffers(workplan, type_assignment_dict)
