"""Execution strategy implementations."""

from __future__ import annotations

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue

__all__ = [
    "ExecutionResult",
    "ExecutionStrategy",
    "DaskExecutionStrategy",
    "PageResult",
    "PreparedPlan",
    "SequenceValue",
    "StrictExecutionStrategy",
]


def __getattr__(name: str):
    if name == "DaskExecutionStrategy":
        from voxlogica.execution_strategy.dask import DaskExecutionStrategy

        return DaskExecutionStrategy
    if name == "StrictExecutionStrategy":
        from voxlogica.execution_strategy.strict import StrictExecutionStrategy

        return StrictExecutionStrategy
    raise AttributeError(name)
