"""The spill-pending queue must be bounded by writes in flight, not by completions.

Measured on the sixty-case oracle sweep, 2026-09-09: after five and a half hours
the queue held **5,912,075 entries** and throughput had fallen from 568 to 92
nodes per second while the process still consumed 2073% of 2400% CPU. Other
users were at 3%, PSI io `full` was 0.00% and PSI memory `full` 0.32%, so it was
neither the disk, nor the machine, nor another tenant: the CPU was full of
scanning our own list of dead node ids.

Two mechanisms, both visible in `_reclaim_memory` and `_finish`:

  * `_finish` appends to `_spill_pending` on EVERY durable completion, while the
    drain runs only `if over_budget`. Under budget the queue therefore grows
    without ever being emptied, and most of what accumulates refers to values
    long since evicted -- the `nid not in self.table.values: continue` branch.
  * the drain is capped at `_EVICT_SWEEP` entries per pass, so once the dead
    entries number in the millions the live ones are buried behind them and each
    pass spends its whole budget discarding corpses. The same file already
    records this failure mode for another queue: "sharing one FIFO buried the
    durable ones tens of thousands of entries deep".

The invariant this pins is therefore about SHAPE, not about a threshold: the
queue's length must be a function of how many writes are outstanding, and must
not grow with the number of completed nodes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase


def _run(source: str, backend, **kwargs):
    plan = reduce_program(parse_program_content(source)).to_symbolic_plan()
    engine = ComputationEngine(backend=backend, **kwargs)
    engine.adopt_plan(plan)
    queries = [engine.submit(g.id, g.operation, g.name) for g in plan.goals]
    asyncio.run(engine.run())
    return engine, plan, queries


@pytest.mark.unit
def test_spill_queue_does_not_grow_with_completions(tmp_path: Path) -> None:
    """Many durable completions, comfortably under budget, and no accumulation.

    The engine must not be left holding one queue entry per node it has ever
    finished. With a store attached and a budget nothing comes close to, the
    drain must still run: it is the `if over_budget` gate that turned this into
    an unbounded structure.
    """
    store = SQLiteResultsDatabase(tmp_path / "s.db")
    # `spin` with rounds>=2 costs enough milliseconds to pass the persist
    # worth-it gate, so every one of these completions is a durable one and
    # therefore a queue entry under the old behaviour.
    engine, plan, _queries = _run(
        'import "test"\n'
        'print "total" fold + (for i in range(0, 400) do spin(i, 2))\n',
        store, max_live_bytes=8 << 30)          # budget far above anything used
    try:
        completions = len(engine.table.completed)
        pending = len(engine._spill_pending)
        assert completions > 300, f"expected a few hundred completions, got {completions}"
        # THE INVARIANT. Not "small" but "not proportional": a queue that holds
        # one entry per completion is the defect, whatever the constant.
        assert pending <= completions // 10, (
            f"{pending} entries left for {completions} completions -- the "
            f"spill-pending queue is growing with completions rather than with "
            f"writes in flight")
    finally:
        store.close()


@pytest.mark.unit
def test_spill_queue_drains_when_under_budget(tmp_path: Path) -> None:
    """The drain must not be conditional on being over budget.

    This is the precise line that made the queue unbounded:

        limit = min(len(self._spill_pending), _EVICT_SWEEP) if over_budget else 0

    Under budget the limit is zero, so nothing is ever removed. A run that never
    approaches its budget must still finish with the queue drained.
    """
    store = SQLiteResultsDatabase(tmp_path / "s2.db")
    engine, _plan, _queries = _run(
        'import "test"\n'
        'print "total" fold + (for i in range(0, 200) do spin(i, 2))\n',
        store, max_live_bytes=8 << 30)
    try:
        assert engine.governor.pressure < 0.5, (
            "this test is only meaningful under budget; the governor reports "
            f"pressure {engine.governor.pressure}")
        assert len(engine._spill_pending) <= 32, (
            f"{len(engine._spill_pending)} entries remain after a run that "
            f"never went over budget")
    finally:
        store.close()


@pytest.mark.unit
def test_the_census_does_not_materialise_the_queue(tmp_path: Path) -> None:
    """Counting must not cost O(queue).

    `_resident_census` built `set(self._spill_pending)` on every memory-log
    snapshot. At 5.9 million entries that is a six-million-element set
    constructed every few seconds, on the very thread whose job is to observe
    cheaply. The census must answer from lengths and membership tests, never by
    copying the queue.
    """
    import inspect

    source = inspect.getsource(ComputationEngine._resident_census)
    assert "set(self._spill_pending)" not in source, (
        "the census materialises the spill queue; at millions of entries that "
        "is the observer becoming the load")
