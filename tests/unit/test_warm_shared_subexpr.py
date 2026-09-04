"""Warm-cache reuse of a subexpression shared by two goals must not crash.

Regression for the DoubleComputationError that fired when a cache-resident node
was also a shared dependency of >=2 goals in the new run: the node could enter
NodeTable.values (via disk load or a shared path) after being enqueued, and the
worker then called begin() on an already-materialized node. See ISSUE.md.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

# `shared` is BOTH a goal (so it is enqueued, not pruned, on the warm run) and a
# dependency of another goal (so a sibling worker loads it into values via
# rematerialize). On the warm run it is also cache-resident. That is the exact
# combination from ISSUE.md that let an already-materialized node reach begin().
PROGRAM = """
import "simpleitk"
import "geom"
import "arrays"
shared = Add(blank(300, 300, 1.0), blank(300, 300, 2.0))
print "shared" array_stats(shared)
print "s" shared
print "a" array_stats(shared)
print "b" count_pixels(shared)
"""


def _run(db_path: Path):
    backend = SQLiteResultsDatabase(db_path=str(db_path))
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            result = ExecutionEngine(storage_backend=backend, use_engine=True).execute_workplan(
                reduce_program(parse_program_content(PROGRAM))
            )
    finally:
        backend.close()
    # Compare only the scalar/dict goals; the image goal "s" (which exists solely
    # to make `shared` a scheduled — hence enqueued — goal) has a non-deterministic
    # repr and is not part of the correctness check.
    goals = {"shared", "a", "b"}
    printed = sorted(l for l in buffer.getvalue().splitlines() if l.split("=", 1)[0] in goals)
    return result, printed


@pytest.mark.unit
def test_worker_forwards_already_materialized_node() -> None:
    """Deterministic guard: a node materialized after it was enqueued (the warm
    race, here simulated by pre-seeding its value) must be forwarded, not passed
    to begin() — which would raise DoubleComputationError."""
    pytest.importorskip("SimpleITK")
    import asyncio

    from voxlogica.engine.core import ComputationEngine

    program = 'import "geom"\nimport "arrays"\nprint "r" array_stats(blank(10, 10, 1.0))\n'
    plan = reduce_program(parse_program_content(program))
    engine = ComputationEngine(backend=None)
    engine.adopt_plan(plan)
    goal = plan.goals[0]
    engine.submit(goal.id, goal.operation, goal.name)
    # Materialize the goal's value out-of-band (as a disk reload / shared path
    # would) after submission but before the worker runs it.
    engine.table.set_value(goal.id, {"mean": 1.0})

    asyncio.run(engine.run())  # must not raise DoubleComputationError

    assert goal.id in engine.table.completed


@pytest.mark.unit
def test_warm_shared_subexpression_completes(tmp_path: Path) -> None:
    pytest.importorskip("SimpleITK")
    db = tmp_path / "shared.db"
    cold_result, cold_out = _run(db)   # populates the shared subexpression
    warm_result, warm_out = _run(db)   # shared is now cache-resident AND shared by two goals

    assert cold_result.success, cold_result.failed_operations
    assert warm_result.success, warm_result.failed_operations  # must not raise DoubleComputationError
    assert warm_out == cold_out and warm_out  # byte-identical, both goals produced
    assert warm_result.cache_summary["recomputes"] == 0
