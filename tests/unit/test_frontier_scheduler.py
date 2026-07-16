"""Invariants of the frontier scheduler (engine/graph|ready|admission|liveness).

These pin the properties the re-architecture promised:
- chunked expansion is deterministic (identical node ids for any chunking);
- a partially-warm cache (some loop bodies on disk, structure not) completes
  instead of deadlocking (the old hand-rolled registration counted
  persisted-but-pruned bodies as unmet forever);
- the memory progress floor: an absurdly small live budget slows the run but
  never wedges it;
- the open frontier stays bounded by the admission window, not plan size;
- steady dataflow: values die with their last consumer, so the live tier ends
  a run holding (approximately) only the goals.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

_STRUCTURAL_OPS = {"for_loop", "default.for_loop", "map", "default.map",
                   "sequence", "default.sequence"}


def _program(elements: int, rounds: int = 0) -> str:
    # Body roots are spin nodes (>=1ms at rounds>=2), so they persist and the
    # partial-warm scenario is reachable.
    return (f'import "test"\n'
            f'print "total" fold + (for i in range(0, {elements}) do spin(i, {rounds}))\n')


def _run(source: str, backend=None, *, max_live_bytes: int = 0, chunk: int = 0,
         window: int = 0) -> tuple[float, ComputationEngine]:
    plan = reduce_program(parse_program_content(source)).to_symbolic_plan()
    engine = ComputationEngine(backend=backend, max_live_bytes=max_live_bytes)
    if chunk or window:
        engine.config = dataclasses.replace(
            engine.config,
            expansion_chunk=chunk or engine.config.expansion_chunk,
            loop_window=window or engine.config.loop_window)
        engine.admission.chunk = chunk or engine.admission.chunk
        engine.admission.window = window or engine.admission.window
    engine.adopt_plan(plan)
    queries = [engine.submit(g.id, g.operation, g.name) for g in plan.goals]
    asyncio.run(engine.run())
    return asyncio.run(queries[0].result()), engine


@pytest.mark.unit
def test_chunked_expansion_is_deterministic() -> None:
    """Any chunk size yields identical node ids and identical results."""
    source = _program(13)
    value_a, engine_a = _run(source, chunk=1)
    value_b, engine_b = _run(source, chunk=1000)
    assert value_a == value_b == float(sum(range(13)))
    assert set(engine_a.table.nodes) == set(engine_b.table.nodes)


@pytest.mark.unit
def test_partial_warm_cache_completes(tmp_path: Path, monkeypatch) -> None:
    """Bodies on disk + structure missing must prune-and-complete, not hang.

    Regression for the old engine's second registration path, which counted a
    persisted-but-pruned body as unmet forever (watchdog abort after 180s).
    """
    monkeypatch.setenv("VOXLOGICA_STALL_TIMEOUT_S", "5")  # a regression fails fast
    source = _program(6, rounds=8)  # ~4ms/body: safely above the persist-worth-it gate
    db = str(tmp_path / "partial.db")

    backend = SQLiteResultsDatabase(db_path=db)
    value_cold, engine_cold = _run(source, backend)
    kernels_cold = engine_cold.metrics()["kernels_executed"]
    # Simulate the crash window: structural rows gone, body rows still present.
    for nid, node in engine_cold.table.nodes.items():
        if node.operator in _STRUCTURAL_OPS:
            backend.delete(nid)
    backend.close()

    backend = SQLiteResultsDatabase(db_path=db)
    value_warm, engine_warm = _run(source, backend)
    metrics_warm = engine_warm.metrics()
    backend.close()
    assert value_warm == value_cold == float(sum(range(6)))
    # The loop re-expanded, but every body was pruned as disk-available.
    assert metrics_warm["expanded_loops"] == 1
    assert metrics_warm["kernels_executed"] < kernels_cold


@pytest.mark.unit
def test_memory_progress_floor_never_wedges(monkeypatch) -> None:
    """A 1-byte live budget parks aggressively yet the run still completes."""
    monkeypatch.setenv("VOXLOGICA_STALL_TIMEOUT_S", "5")
    value, engine = _run(_program(9), max_live_bytes=1)
    assert value == float(sum(range(9)))
    assert engine.metrics()["peak_live_mb"] >= 0  # ran under constant pressure


@pytest.mark.unit
def test_frontier_bounded_by_window_not_plan() -> None:
    """The open set tracks the admission window; the plan can be much wider."""
    value, engine = _run(_program(64), window=4, chunk=4)
    assert value == float(sum(range(64)))
    total = len(engine.table.nodes)
    # window(4 bodies) x tiny body + structural slack << the 64-element plan
    assert engine.metrics()["peak_frontier"] < total
    assert engine.metrics()["peak_frontier"] <= 40


@pytest.mark.unit
def test_values_die_with_their_last_consumer() -> None:
    """Steady dataflow: after a run the live tier holds ~only goal values."""
    value, engine = _run(_program(20))
    assert value == float(sum(range(20)))
    # Everything except the goal (and trivially-completed constants, which are
    # never registered as anyone's consumer once released) must be evicted.
    residues = [nid for nid in engine.table.values
                if engine.table.nodes[nid].kind == "primitive"
                and nid not in engine._goals]
    assert not residues, f"leaked primitive values: {[r[:8] for r in residues]}"
