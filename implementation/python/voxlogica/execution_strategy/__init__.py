"""Execution strategy package.

The DAG-only branch exposes a single strict in-process strategy, but this
package boundary keeps the planning and runtime concerns decoupled.
"""

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.execution_strategy.sequential import SequentialExecutionStrategy
from voxlogica.execution_strategy.parallel import ParallelExecutionStrategy
from voxlogica.execution_strategy.lazy import LazyExecutionStrategy

__all__ = [
    "ExecutionResult",
    "ExecutionStrategy",
    "PageResult",
    "PreparedPlan",
    "SequentialExecutionStrategy",
    "ParallelExecutionStrategy",
    "LazyExecutionStrategy",
    "SequenceValue",
]
