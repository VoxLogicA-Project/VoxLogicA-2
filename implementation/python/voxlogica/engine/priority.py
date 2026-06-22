"""Query priorities for the live computation engine.

A query carries a priority; every node it demands inherits the maximum priority
over the queries that demand it. Raising a query's priority therefore moves its
unfinished dependencies ahead of lower-priority work in the ready queue.
"""

from __future__ import annotations

from enum import IntEnum


class Priority(IntEnum):
    """Relative scheduling urgency; higher runs first."""

    LOW = 0
    NORMAL = 10
    HIGH = 20
    INTERACTIVE = 30
