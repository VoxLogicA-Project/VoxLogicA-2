"""Query handles returned by the computation engine.

A query is one submitted goal (a node to materialize plus an optional
side-effect such as print/save). The handle exposes its live status and an
awaitable result, and is the unit the user can reprioritise or cancel.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from voxlogica.engine.priority import Priority
from voxlogica.lazy.ir import NodeId


class QueryStatus(str, Enum):
    """Lifecycle of a submitted query."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Query:
    """One submitted goal tracked by the engine."""

    id: int
    node_id: NodeId
    operation: str = "value"
    name: str = ""
    priority: Priority = Priority.NORMAL
    status: QueryStatus = QueryStatus.PENDING
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    _value: Any = None
    _error: BaseException | None = None

    def _settle(self, status: QueryStatus, value: Any = None, error: BaseException | None = None) -> None:
        """Record the terminal outcome and wake any awaiters."""
        self.status = status
        self._value = value
        self._error = error
        self._done.set()

    async def result(self) -> Any:
        """Await the query's value, raising if it failed."""
        await self._done.wait()
        if self._error is not None:
            raise self._error
        return self._value
