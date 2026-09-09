"""The results store is a cache, not a correctness requirement.

`node_table.py` states the contract in its own module docstring: with a
persistent backend an evicted value is reloaded, and without one "an evicted
value is simply recomputed on demand". That property is what makes the store a
cache. It did not hold.

Measured on `brats-threshold-sweep-aiim.imgql` (20 cases, 16 goals) on the
24-core reference host: with a real store, 16 of 16 goals, three runs out of
three. With `--no-cache`, `--no-write-cache` or `--sparse-cache`, 13 of 16 --
and a fourth, independent run of `--no-cache` lost 7, ending in
`error[E_INTERNAL]`. Always failing, by a varying amount.

The goals that vanished were exactly the ones reading back into a lazily built
sequence (`index(s, argmax(s))` and the outlier ranking on it); the survivors
were the aggregates that reduce straight to scalars. The traceback named the
mechanism:

    strategy.py _side_effect -> _materialize -> handles.resolve_deep
      -> _rebuild -> core._resolve_reference -> core._rematerialize
    NeedsExpansion: 903d965f... must be expanded, not computed

A loop node's value comes from the sequence it splices into, reached through
`_alias`. The worker POPPED that entry when it forwarded the value, so after
that turn the only road back to a loop node's value was gone -- and a loop node
is the one thing `_rematerialize` cannot rebuild, because `default.for_loop`'s
kernel fails on the closure the engine never builds. With a store the value came
back off disk before that mattered and nobody noticed; without one, a goal's own
side effect raised.
"""

from __future__ import annotations

import asyncio

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import NoCacheStorageBackend

_LOOP_OPS = {"for_loop", "default.for_loop"}


def _run(source: str, *, max_live_bytes: int = 0):
    plan = reduce_program(parse_program_content(source)).to_symbolic_plan()
    engine = ComputationEngine(backend=NoCacheStorageBackend(),
                               max_live_bytes=max_live_bytes)
    engine.adopt_plan(plan)
    queries = [engine.submit(g.id, g.operation, g.name) for g in plan.goals]
    asyncio.run(engine.run())
    return engine, plan, queries


def _loop_nodes(engine: ComputationEngine) -> list[str]:
    return [nid for nid, node in engine.table.nodes.items()
            if (node.operator or "") in _LOOP_OPS]


@pytest.mark.unit
def test_a_loop_nodes_value_survives_losing_its_own_value_with_no_store() -> None:
    """Evict a loop node with no store behind it, then ask for it again.

    This is the defect exactly: `_resolve_reference` follows `_alias` to answer
    for a loop node whose own value is gone, so the alias has to outlive the
    forward that consumed it. Before the fix this raised
    ``NeedsExpansion: ... must be expanded, not computed`` -- from a goal's side
    effect, in a run with no store to hide it.
    """
    engine, _plan, _queries = _run('import "test"\n'
                                   'print "total" fold + (for i in range(0, 8) do spin(i, 1))\n')
    loops = _loop_nodes(engine)
    assert loops, "the program must contain a loop node for this test to mean anything"

    for loop_id in loops:
        assert loop_id in engine._alias, (
            "the alias from a loop node to its spliced sequence was dropped; "
            "without it a loop node's value cannot be recovered at all")
        engine.table.evict(loop_id)                    # what memory pressure does
        value = engine._resolve_reference(loop_id)     # what a goal's side effect does
        assert value is not None


@pytest.mark.unit
def test_the_forward_still_happens_exactly_once() -> None:
    """Keeping the alias must not turn one forward into two.

    The pop was doing two jobs: remembering the mapping, and marking the forward
    as done. Splitting them is only correct if the second still holds -- the
    forward takes a hold on the sequence and releases it, so forwarding twice
    would release a hold it does not have.
    """
    engine, _plan, _queries = _run('import "test"\n'
                                   'print "total" fold + (for i in range(0, 6) do spin(i, 1))\n')
    loops = _loop_nodes(engine)
    assert loops
    for loop_id in loops:
        assert loop_id in engine._forwarded, "the loop node was never forwarded"
    # Every forwarded node is still aliased, and nothing is forwarded twice.
    assert engine._forwarded <= set(engine._alias)
    assert len(engine._forwarded) == len(set(engine._forwarded))


@pytest.mark.unit
def test_goals_reading_back_into_a_sequence_complete_with_no_store() -> None:
    """The shape of every goal that vanished: index at a computed position.

    `aggregate` reduces to a scalar and always survived; `readback` reaches into
    the sequence, and on the real sweep its analogues (`best_dice`,
    `best_vi_thr`, and the outlier ranking built on them) were exactly what went
    missing.
    """
    source = ('import "test"\n'
              'let s = for i in range(0, 12) do spin(i, 1)\n'
              'print "aggregate" fold + s\n'
              'print "readback"  index(s, argmax(s))\n')
    engine, plan, queries = _run(source, max_live_bytes=1 << 18)
    values = [asyncio.run(query.result()) for query in queries]
    assert len(values) == len(plan.goals) == 2
    assert engine._first_error is None, engine._first_error
