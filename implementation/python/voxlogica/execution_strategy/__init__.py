"""Execution strategy implementations."""

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.dask import DaskExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.execution_strategy.strict import StrictExecutionStrategy

__all__ = [
    "ExecutionResult",
    "ExecutionStrategy",
    "DaskExecutionStrategy",
    "PageResult",
    "PreparedPlan",
    "SequenceValue",
    "StrictExecutionStrategy",
]
