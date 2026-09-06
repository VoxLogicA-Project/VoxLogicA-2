"""Pressure shedding: if a value would have to queue, it is not written.

The rule has one safety condition, and it is the whole reason the tests below
are worth having: only a value the engine could REBUILD may be dropped. A loop
or sequence node is computed by the expansion machinery rather than by a
kernel, so shedding it would lose it -- the same invariant `NodeTable.evict`
enforces on the way out.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.persist import AsyncPersister


class _Backend:
    """A backend that records what reached it and nothing more."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def put_success_batch(self, entries) -> None:
        self.written.extend(entry[0] for entry in entries)

    def has(self, node_id) -> bool:
        return False


@pytest.fixture
def persister():
    p = AsyncPersister(_Backend(), max_pending_bytes=1 << 30)
    yield p
    p.close() if hasattr(p, "close") else None


@pytest.mark.unit
def test_nothing_is_shed_without_a_recompute_probe(persister) -> None:
    """No probe means the engine cannot say what is rebuildable, so nothing is
    dropped. Shedding a value that cannot be rebuilt loses it outright."""
    persister._queue.put(("filler", None, {}, 0, 0.0, (), None))
    persister._num_writers = 0          # every writer busy: this WOULD queue
    persister.submit("n1", b"x", {}, compute_ms=1.0, size=10)
    assert persister.shed_pressure == 0


@pytest.mark.unit
def test_a_rebuildable_value_is_shed_when_it_would_queue(persister) -> None:
    persister.set_recompute_probe(lambda nid: True)
    persister._queue.put(("filler", None, {}, 0, 0.0, (), None))
    persister._num_writers = 0
    persister.submit("n1", b"x", {}, compute_ms=1.0, size=1234)
    assert persister.shed_pressure == 1
    assert persister.shed_bytes == 1234


@pytest.mark.unit
def test_a_value_that_cannot_be_rebuilt_is_never_shed(persister) -> None:
    """A `for_loop` value has no kernel to come back from: it must be written
    however long the queue is."""
    persister.set_recompute_probe(lambda nid: False)
    persister._queue.put(("filler", None, {}, 0, 0.0, (), None))
    persister._num_writers = 0
    persister.submit("loop-node", b"x", {}, compute_ms=1.0, size=1234)
    assert persister.shed_pressure == 0


@pytest.mark.unit
def test_nothing_is_shed_while_a_writer_is_free(persister) -> None:
    """The queue depth is the whole signal: with a writer idle, the value does
    not wait, so there is nothing to trade away."""
    persister.set_recompute_probe(lambda nid: True)
    persister._num_writers = 8          # queue is empty, writers idle
    persister.submit("n1", b"x", {}, compute_ms=1.0, size=10)
    assert persister.shed_pressure == 0


@pytest.mark.unit
def test_a_failing_probe_never_sheds(persister) -> None:
    """A dying engine must not start dropping values it cannot reason about."""
    def angry(_nid):
        raise RuntimeError("engine is going down")

    persister.set_recompute_probe(angry)
    persister._queue.put(("filler", None, {}, 0, 0.0, (), None))
    persister._num_writers = 0
    persister.submit("n1", b"x", {}, compute_ms=1.0, size=10)
    assert persister.shed_pressure == 0
