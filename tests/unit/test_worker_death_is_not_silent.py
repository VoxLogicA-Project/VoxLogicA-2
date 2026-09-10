"""A worker that dies must not take a run-completion unit with it, or be silent.

MEASURED, through the live control channel, at the stall this comes from::

    ready              0
    ready_parked       0
    in_flight          0
    admission_jobs     0
    ready_outstanding  8
    incomplete         1157

Eight run-completion units held by nothing at all. `ready.outstanding` is what
`wait_idle` joins on: a queued node, a parked node and an active expansion job
each hold one. Zero in every tier and eight outstanding means eight units had
been taken and never returned -- so the run could neither finish (the count
never reaches zero) nor progress (nothing was left to push).

The cause was two lines in `_worker`'s `finally`::

    self._maintain()
    self.ready.end_unit()

`_maintain` is best-effort maintenance -- it samples memory, trims the buffer
pool, unparks and nudges paused unrolls -- and anything it raised skipped
`end_unit` and then escaped `_worker`. Since the worker tasks are never awaited
(the run joins on the unit count, and they are only cancelled at the end),
asyncio collected the exception and reported it nowhere. Every such failure cost
one unit and one producer, silently.

Two properties, both violated:

  1. the unit is returned however the turn ends;
  2. a worker's death becomes the run's error, so the run drains and says what
     happened instead of hanging.
"""

from __future__ import annotations

import asyncio

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.ready import ReadyQueue


class _Admission:
    def __init__(self) -> None:
        self.aborted: BaseException | None = None
        self.active_jobs = 0

    def abort(self, exc):
        self.aborted = exc


class _Table:
    def __init__(self) -> None:
        self.completed: set[str] = set()
        self.nodes: dict[str, object] = {}

    def is_running(self, nid):
        return False


def _engine(maintain_raises: BaseException | None) -> ComputationEngine:
    engine = object.__new__(ComputationEngine)
    engine.ready = ReadyQueue()
    engine.table = _Table()
    engine.admission = _Admission()
    engine._first_error = None
    engine._observe = None
    engine._alias = {}
    engine._forwarded = set()
    engine._priority = {}
    engine._in_flight = 0
    engine._reports = []

    def maintain():
        if maintain_raises is not None:
            raise maintain_raises

    engine._maintain = maintain
    engine._report = lambda *a, **k: None

    def fail_node(nid, exc):
        if engine._first_error is None:
            engine._first_error = exc
            engine.admission.abort(exc)

    engine._fail_node = fail_node
    return engine


@pytest.mark.unit
def test_the_unit_is_returned_even_when_maintenance_raises() -> None:
    """This is the arithmetic that hung the run: the count must reach zero."""
    engine = _engine(RuntimeError("dictionary changed size during iteration"))

    async def drive() -> None:
        engine.ready.push("n1", 0)
        assert engine.ready.outstanding == 1
        worker = asyncio.ensure_future(engine._worker())
        # `n1` is marked completed, so the turn takes the duplicate-skip path
        # and the only thing left in it is the `finally`.
        engine.table.completed.add("n1")
        await asyncio.wait_for(engine.ready.wait_idle(), timeout=5.0)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert engine.ready.outstanding == 0, (
        "the run-completion unit was lost, so wait_idle can never resolve")


@pytest.mark.unit
def test_a_maintenance_failure_becomes_the_runs_error() -> None:
    """Reported, not swallowed: a silent maintenance failure is a hang."""
    boom = RuntimeError("trim_pool exploded")
    engine = _engine(boom)

    async def drive() -> None:
        engine.ready.push("n1", 0)
        engine.table.completed.add("n1")
        worker = asyncio.ensure_future(engine._worker())
        await asyncio.wait_for(engine.ready.wait_idle(), timeout=5.0)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert engine._first_error is boom
    assert engine.admission.aborted is boom


@pytest.mark.unit
def test_the_worker_survives_a_maintenance_failure_and_keeps_serving() -> None:
    """One bad turn must not subtract a producer for the rest of the run.

    The stall had eight fewer workers than the run believed it had. Even with
    the error recorded, the worker must return to `pop` rather than unwind.
    """
    engine = _engine(RuntimeError("transient"))

    async def drive() -> None:
        worker = asyncio.ensure_future(engine._worker())
        for name in ("n1", "n2", "n3"):
            engine.table.completed.add(name)
            engine.ready.push(name, 0)
        await asyncio.wait_for(engine.ready.wait_idle(), timeout=5.0)
        assert not worker.done(), (
            "the worker unwound on a maintenance failure instead of taking the "
            "next node")
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert engine.ready.outstanding == 0


@pytest.mark.unit
def test_a_clean_turn_still_returns_its_unit_exactly_once() -> None:
    engine = _engine(None)

    async def drive() -> None:
        worker = asyncio.ensure_future(engine._worker())
        engine.table.completed.add("n1")
        engine.ready.push("n1", 0)
        await asyncio.wait_for(engine.ready.wait_idle(), timeout=5.0)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert engine.ready.outstanding == 0
    assert engine._first_error is None
