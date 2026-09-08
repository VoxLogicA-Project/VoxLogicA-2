"""A node offered to the workers twice must not kill the run.

Reported as a non-deterministic failure of a one-minute nnU-Net sweep:

    DoubleComputationError: node <id> already running
    NodeExecutionError: simpleitk.ReadImage failed while evaluating node <id>

with three properties that together name the cause: the victim node changes
from run to run, it is always `ReadImage` (the operator with the most concurrent
instances in flight), and it disappears entirely at `--threads 1`.

`ComputationEngine._worker` pops a node and calls `table.begin`, which raises on
a second claim. Between the pop and the `begin` there is no `await`, so two
coroutines cannot interleave there: the same id must have been OFFERED twice.
Two paths do exactly that, and both guard the registration while leaving the
push unguarded:

    core.py `_await_named_deps`:   if ref not in self.graph.incomplete:
                                       self.graph.register(ref)
                                   self.ready.push(ref, priority)

    core.py `_await_expansion`:    if to_expand not in self.graph.incomplete:
                                       self.graph.register(to_expand)
                                   self.ready.push(to_expand, priority)

`graph.incomplete` holds every registered-and-unfinished node, INCLUDING one a
worker is running right now, so both of these re-offer a node already in flight.
At one worker the duplicate is popped only after the first computation finished
and `nid in self.table.completed` absorbs it, which is why the bug needs
concurrency to show. `_await_named_deps` is reached only for a dependency whose
value CONTAINS HANDLES -- a lazily built sequence -- and the nodes its handles
name in that sweep are the per-case image reads, which is why the victim is
always `ReadImage`.
"""

from __future__ import annotations

import asyncio

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


def _engine(source: str) -> tuple[ComputationEngine, list]:
    plan = reduce_program(parse_program_content(source)).to_symbolic_plan()
    engine = ComputationEngine()
    engine.adopt_plan(plan)
    queries = [engine.submit(g.id, g.operation, g.name) for g in plan.goals]
    return engine, queries


def _node_by_operator(engine: ComputationEngine, needle: str) -> str:
    for nid, node in engine.table.nodes.items():
        if needle in (node.operator or ""):
            return nid
    raise AssertionError(f"no {needle} node in the plan")


@pytest.mark.unit
def test_a_duplicated_ready_entry_does_not_fail_the_run() -> None:
    """The ready queue is a HINT, not ownership of a node.

    Several paths legitimately re-offer a node (an evicted input asked for
    again, a graph-growing node put back on the frontier), and none of them can
    know whether a worker is already on it -- `graph.incomplete` does not
    distinguish "queued" from "running". So a second offer must cost at most a
    wasted pop.
    """
    engine, queries = _engine('import "test"\nprint "x" spin(1, 4000)\n')
    engine.ready.push(_node_by_operator(engine, "spin"), 0)   # the second offer
    asyncio.run(engine.run())
    assert asyncio.run(queries[0].result()) is not None
    assert engine._first_error is None, engine._first_error


@pytest.mark.unit
def test_the_frontier_paths_never_offer_a_running_node() -> None:
    """`_await_expansion` must not push a node that is already claimed.

    The unit-level statement of the same defect: `is_claimable` is exactly the
    question "would `begin` succeed", and a node a worker is running answers no.
    """
    engine, _queries = _engine('import "test"\nprint "x" spin(1, 1)\n')
    running = _node_by_operator(engine, "spin")
    waiting = next(nid for nid in engine.table.nodes if nid != running)

    engine.table.begin(running)          # a worker is on it, right now
    before = engine.ready.qsize()
    engine._await_expansion(waiting, running)

    assert engine.table.is_claimable(running) is False
    assert engine.ready.qsize() == before, (
        "a node already being computed was offered to the workers again")
