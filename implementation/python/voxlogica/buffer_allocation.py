"""
Static Buffer Allocation for DAG Execution

This module implements the static buffer reuse algorithm.
Given a workplan (DAG), a type assignment function, and a type compatibility function,
it computes an optimal buffer allocation that minimizes memory usage while ensuring
correctness (no overlapping lifetimes and type compatibility).
"""

# https://www.researchgate.net/publication/244252294_SIRA_Schedule_Independent_Register_Allocation_for_Software_Pipelining

from typing import Dict, List, Callable, Any, Set, Optional
from .reducer import WorkPlan, Operation, OperationId, Goal
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

        It is assumed that the workplan nodes are in topological order.

    Returns:
        Dictionary mapping operation IDs to buffer IDs (integers starting from 0)
    """
    raise Exception("STUB")
