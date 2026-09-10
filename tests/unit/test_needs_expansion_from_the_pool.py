"""`NeedsExpansion` out of a kernel dispatch is a miss, not a node failure.

The exception's own docstring says what it is: "an outcome, not a failure: the
value is still obtainable, just not by the road the caller took." Two of the
three places a worker can meet it already recover -- register the node so
admission expands it, and requeue the waiter behind it. The third, the one
raised from INSIDE `executor.run`, did not.

It gets there through the kernel's own argument resolution:

    executor._compute -> _compute_node -> _eager -> handles.resolve_deep
      -> core._resolve_reference -> core._rematerialize -> NeedsExpansion

on a pool thread, which can neither register a node nor requeue a turn. So it
propagated, `_fail_node` wrapped it, and the run died. Measured on the
sixty-case oracle sweep: killed at 4 min 10 s with all seven goals already
open, on

    operator='default.for_loop' interned=True completed=False alias=False
    forwarded=False incomplete=False persisted=False

-- a loop node interned in this run's table and registered with nothing at all.

The recovery has one subtlety worth a test of its own: `table.begin` has
already taken the single-computation claim, and the retry after the expansion
must be able to take it again. Giving it back is `abandon`, NOT
`complete_without_value` -- the latter asserts a value was produced and would
let `_schedule_subgraph` prune the node for the rest of the run.
"""

from __future__ import annotations

import asyncio

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.evaluation import NeedsExpansion
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeSpec


@pytest.mark.unit
def test_abandon_gives_the_claim_back_without_claiming_a_value() -> None:
    table = NodeTable(backend=None)
    table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
    table.begin("n1")
    assert table.is_running("n1")
    table.abandon("n1")
    assert not table.is_running("n1")
    assert "n1" not in table.completed, (
        "abandon must not assert the value was computed; the node has to be "
        "schedulable again, and `completed` is what prunes it")
    table.begin("n1")          # the retry must be able to claim it again


@pytest.mark.unit
def test_complete_without_value_is_not_the_same_thing() -> None:
    """Guards the choice above: these two must not be confused again."""
    table = NodeTable(backend=None)
    table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
    table.begin("n1")
    table.complete_without_value("n1")
    assert "n1" in table.completed


class _Graph:
    def __init__(self) -> None:
        self.incomplete: set[str] = set()
        self.consumers: dict[str, int] = {}
        self.registered: list[str] = []
        self.waits: list[tuple[str, str]] = []

    def deps(self, nid):
        return frozenset()

    def register(self, nid):
        self.registered.append(nid)
        self.incomplete.add(nid)
        return True

    def await_one(self, nid, dep):
        self.waits.append((nid, dep))
        return True

    def names_handles(self, nid):
        return False


class _Ready:
    def __init__(self) -> None:
        self.items: list[tuple[str, int]] = []
        self.pushed: list[tuple[str, int]] = []

    def push(self, nid, priority):
        self.pushed.append((nid, priority))
        self.items.append((nid, priority))

    async def pop(self):
        while not self.items:
            await asyncio.sleep(0)
        return self.items.pop(0)[0]

    def qsize(self):
        return len(self.items)

    def end_unit(self, source="job"):
        pass


@pytest.mark.unit
def test_a_dispatch_that_raises_needs_expansion_is_requeued_not_failed() -> None:
    """The whole property, driven through `_worker` itself."""
    engine = object.__new__(ComputationEngine)
    table = NodeTable(backend=None)
    table.nodes["want"] = NodeSpec(kind="primitive", operator="default.argmax")
    table.nodes["loop9"] = NodeSpec(kind="primitive", operator="default.for_loop")
    engine.table = table
    engine.graph = _Graph()
    engine.ready = _Ready()
    engine._first_error = None
    engine._alias = {}
    engine._forwarded = set()
    engine._priority = {"want": 3}
    engine._in_flight = 0
    engine.max_concurrency = 4
    engine._nodes_done = 0
    engine._progress_pending = 0
    engine._peak_frontier = 0
    engine._clock = None
    engine._recomputes = 0
    engine._dispatch_pins = {}
    engine._reload_deferred = set()
    engine._kernels_executed = 0
    engine._observe = None
    engine._progress = None
    engine._report = lambda *a, **k: None
    engine._maintain = lambda: None
    engine._finish = lambda nid, value, **kw: engine.table.completed.add(nid)
    engine._await_named_deps = lambda nid, node: False
    engine._rematerialize = lambda nid: None
    engine.config = type("C", (), {"fusion_enabled": False})()
    # `modes_of` memoizes per registry in a WeakKeyDictionary, so the stub has
    # to be a real object rather than None.
    engine.registry = type("R", (), {
        "get_spec": staticmethod(lambda operator: None)})()
    engine.expander = type("E", (), {"can_expand": staticmethod(lambda node: False)})()
    engine.admission = type("A", (), {"active_jobs": 0})()

    failures: list[tuple[str, BaseException]] = []
    engine._fail_node = lambda nid, exc: failures.append((nid, exc))

    class _Executor:
        """Raises the miss for the dispatched node; anything else succeeds.

        The second turn matters: the recovery pushes `loop9`, a worker pops it,
        and if the recovery had left `want` claimed or `loop9` unregistered the
        run would break there rather than here.
        """

        calls = 0

        async def run(self, tbl, nid):
            _Executor.calls += 1
            if nid == "want" and _Executor.calls == 1:
                raise NeedsExpansion("loop9", "operator='default.for_loop'")
            return b"value"

    engine.executor = _Executor()

    async def drive() -> None:
        engine.ready.push("want", 3)
        worker = asyncio.ensure_future(engine._worker())
        for _ in range(200):                       # let the turn run
            await asyncio.sleep(0)
            if engine.graph.waits:
                break
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())

    assert failures == [], f"the miss was reported as a failure: {failures}"
    assert "loop9" in engine.graph.registered, (
        "the node that must be expanded was never registered, so nothing will "
        "expand it")
    assert ("want", "loop9") in engine.graph.waits, (
        "the dispatched node was not made to wait for the expansion")
    assert not engine.table.is_running("want"), (
        "the single-computation claim was not given back, so the retry after "
        "the expansion will raise DoubleComputationError")
    assert "want" not in engine.table.completed
    assert engine._in_flight == 0, "in-flight accounting drifted"
