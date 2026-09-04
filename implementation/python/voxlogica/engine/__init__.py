"""Live, content-addressed computation engine.

See ``doc/dev/unified-computation-engine.md``. The engine evaluates a Merkle DAG
of expressions under a single reduction semantics, scheduling by query priority
and demoting consumed values through cache tiers.
"""

from __future__ import annotations

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.node_table import DoubleComputationError, NodeTable
from voxlogica.engine.priority import Priority
from voxlogica.engine.query import Query, QueryStatus

__all__ = [
    "ComputationEngine",
    "DoubleComputationError",
    "NodeTable",
    "Priority",
    "Query",
    "QueryStatus",
]
