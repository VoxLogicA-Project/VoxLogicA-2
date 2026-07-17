"""Loop admission: chunked runtime expansion under a bounded window.

A runtime loop (``for_loop``/``map`` over a lazy iterable) may unroll into
hundreds of thousands of nodes. Two rules keep that from ever swamping the
scheduler:

1. **Expansion is incremental and off the event loop.** Bodies are reduced in
   chunks on a dedicated single-thread executor (not the kernel pool: reduction
   is pure Python and holds the GIL; one thread of it pipelines with
   GIL-releasing kernels without competing for kernel slots). The event loop
   only ever does O(chunk) splicing work at a time — the old design reduced
   *every* element in one synchronous call, freezing all dispatch for minutes
   on large plans.
2. **Admission is windowed and demand-driven.** At most ``loop_window``
   bodies of a loop are in flight at once; beyond that, the next body is
   admitted only when the ready queue would otherwise starve the workers
   (queue depth < worker count) — never merely because bytes are under
   budget. A body's own subtree can be thousands of nodes deep, so one
   richly-parallel body can keep every core busy alone; gating on "bytes
   available" instead of "workers fed" used to open the entire window's
   worth of bodies immediately, and peak RSS tracked window x body size
   instead of the one or two bodies actually needed to saturate the
   machine. The hard ceiling remains a backstop (never crossed except to
   break a true wedge). This bounds the *admission burst*; it does not by
   itself bound a loop's *sequence-assembly floor* — see engine/core.py's
   ``_reclaim_memory`` for that.

VALUE-LIFETIME PROTOCOL (why sequence assembly is safe): every reduced body
carries a *stage pin* (one consumer reference) from the moment it exists until
the spliced ``sequence`` node is registered — registration adds the sequence's
own reference per body, after which the stage pins are dropped. So a body value
can never be evicted in the gap between its completion and the sequence node
learning it is a consumer. Loop captures are pinned from closure discovery
until every body has been admitted (each admitted body then holds its own
references), so values shared across bodies are computed once and stay
resident for the whole unroll.

WARM CACHES: a body already materialized on disk is *available* — it takes no
window slot, is never scheduled, and never gates the sequence node (its value
is loaded on demand when the sequence assembles). This is the same
availability rule every other registration uses; the previous engine had a
private copy here that counted persisted-but-pruned bodies as unmet and could
deadlock a partially-warm cache.

DETERMINISM: chunk boundaries and admission order cannot change node identity —
per-element reduction is independent and hash-consing is order-insensitive —
so incremental expansion yields byte-identical ids to monolithic expansion.
"""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from voxlogica.engine.expander import Expander, Expansion
from voxlogica.engine.graph import DependencyGraph
from voxlogica.engine.liveness import LivenessProbe
from voxlogica.engine.ready import ReadyQueue
from voxlogica.lazy.ir import NodeId, NodeSpec


@dataclass
class _Job:
    """One loop's in-flight unroll."""

    loop_id: NodeId
    priority: int
    staged: deque = field(default_factory=deque)  # reduced, awaiting a window slot
    in_flight: int = 0                            # admitted, not yet completed
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class LoopAdmission:
    """Owns every active loop unroll; all state mutation on the event loop."""

    def __init__(self, expander: Expander, graph: DependencyGraph, ready: ReadyQueue,
                 liveness: LivenessProbe, *, window: int, chunk: int, workers: int,
                 hard_live_bytes: int,
                 schedule: Callable[[NodeId, int], None],
                 available: Callable[[NodeId], bool],
                 materialize: Callable[[NodeId], Any],
                 idle: Callable[[], bool],
                 on_spliced: Callable[[NodeId, NodeId, int], None],
                 fail_node: Callable[[NodeId, BaseException], None]):
        self.expander = expander
        self.graph = graph
        self.ready = ready
        self.liveness = liveness
        self.window = max(1, window)
        self.chunk = max(1, chunk)
        self.workers = workers
        self.hard_live_bytes = hard_live_bytes
        self._schedule = schedule
        self._available = available
        self._materialize = materialize
        self._idle = idle
        self._on_spliced = on_spliced
        self._fail_node = fail_node
        self._jobs: dict[NodeId, _Job] = {}
        self._body_owner: dict[NodeId, _Job] = {}
        # Closure-capture holds released when the owning loop finishes expanding.
        self.capture_holds: dict[NodeId, tuple[NodeId, ...]] = {}
        # One thread: reduction is GIL-bound; more threads would only contend.
        self._reducer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxlogica-expand")
        self._aborted: BaseException | None = None
        self.expanded_loops = 0
        self.expanded_bodies = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, nid: NodeId, node: NodeSpec, priority: int) -> None:
        """Launch a loop's expansion as a background job (one ready-queue unit).

        Called from a worker's turn for the loop node; the unit taken here is
        ended by the job itself, so run-completion accounting covers the whole
        expansion even though the worker's own turn ends immediately.
        """
        self.ready.begin_unit()
        asyncio.get_running_loop().create_task(self._run_job(nid, node, priority))

    async def _run_job(self, nid: NodeId, node: NodeSpec, priority: int) -> None:
        job = _Job(loop_id=nid, priority=priority)
        self._jobs[nid] = job
        try:
            iterable = self._materialize(node.args[0])
            expansion = self.expander.prepare(nid, node, iterable)
            if expansion is None:
                raise RuntimeError(f"cannot expand loop node {nid[:12]} ({node.operator})")
            loop = asyncio.get_running_loop()
            cursor = 0
            while cursor < expansion.total:
                await self._room(job)
                stop = min(cursor + self.chunk, expansion.total)
                ids = await loop.run_in_executor(
                    self._reducer, self.expander.reduce_chunk, expansion, cursor, stop)
                cursor = stop
                for body in ids:  # stage pin: value must survive until the sequence holds it
                    self.graph.pin(body)
                    self.liveness.staged.add(body)
                    job.staged.append(body)
                self._admit(job)
            while job.staged:  # drain the tail under the same window pacing
                await self._room(job)
                self._admit(job)
            self._splice(nid, expansion, priority)
        except Exception as exc:  # noqa: BLE001
            self._fail_node(nid, exc)
        finally:
            self._release_captures(node)
            self._jobs.pop(nid, None)
            self.ready.end_unit()

    def _splice(self, nid: NodeId, expansion: Expansion, priority: int) -> None:
        """Register the sequence node and hand each body's stage pin to it.

        The sequence is an ordinary node (its spec's args are the bodies), so
        the single registration rule applies: it gates only on bodies still in
        flight; completed or disk-cached bodies count as available.
        """
        seq_id = self.expander.sequence_id(expansion)
        self.expanded_loops += 1
        self.expanded_bodies += expansion.total
        if seq_id not in self.graph.incomplete and not self._available(seq_id):
            if self.graph.register(seq_id):
                self.ready.push(seq_id, priority)
        for body in expansion.body_ids:  # transfer: sequence now holds its own refs
            self.liveness.staged.discard(body)
            self.graph.release(body)
        self._on_spliced(nid, seq_id, priority)

    # ── Windowed admission ────────────────────────────────────────────────────

    def _has_room(self, job: _Job) -> bool:
        """Whether this loop may admit one more body right now.

        DEMAND-DRIVEN (queue-depth) admission, not byte-budget admission:

        1. window — never more than ``window`` bodies of THIS loop in flight.
        2. hard ceiling — past ``hard_live_bytes`` admission refuses outright,
           except to break a true wedge (nothing running, nothing ready): that
           is what actually bounds peak RSS, a backstop that must never be
           crossed by policy, only by necessity.
        3. demand — otherwise, admit exactly while the ready queue would
           otherwise starve the workers (``qsize < workers``), regardless of
           how far under the soft byte budget we are.

        Why demand, not budget: a single loop body can itself contain a huge
        internal subtree (thousands of nodes keeping every core busy alone), so
        the right question is never "how many *elements* are open" but "are
        there ~N ready nodes to feed N workers". Gating on bytes-under-budget
        (the old rule) said yes almost always — the default budget is a large
        fraction of RAM — so the engine opened the *entire* window's worth of
        elements immediately: peak RSS ~ window x per-element working set,
        independent of whether the workers could even use that concurrency.
        Gating on queue depth instead makes concurrent-element count emergent:
        a richly-parallel element fills the queue alone and no sibling opens;
        a thin one lets the next in as soon as the queue thins. The byte budget
        remains meaningful (see engine/core.py's proactive reclaim, and the
        hard ceiling above) but is no longer the *admission* valve — admission
        can't reclaim memory already committed to open work in the first
        place, only reclaiming what is already resident can.
        """
        if job.in_flight >= self.window:
            return False
        accounted = self.graph.table.accounted_bytes
        if accounted >= self.hard_live_bytes:
            return self._idle()  # at the ceiling: admit only to break a true wedge
        return self.ready.qsize() < self.workers

    async def _room(self, job: _Job) -> None:
        """Wait until the window has a free slot (or the progress floor opens it).

        Guaranteed to resolve: a paused job either has bodies in flight (their
        completions wake it) or is blocked purely on memory with a non-starving
        queue (so workers are busy and completions are coming).
        """
        while not self._has_room(job):
            if self._aborted is not None:
                raise self._aborted
            job.wake.clear()
            await job.wake.wait()
        if self._aborted is not None:
            raise self._aborted

    def _admit(self, job: _Job) -> None:
        """Move staged bodies into the schedule while the window allows."""
        while job.staged and self._has_room(job):
            body = job.staged.popleft()
            self.liveness.staged.discard(body)
            if body in self.graph.incomplete:      # shared with another goal/loop
                self._body_owner.setdefault(body, job)
                job.in_flight += 1
            elif self._available(body):            # completed or on disk: no slot
                continue
            else:
                self._body_owner[body] = job
                job.in_flight += 1
                self._schedule(body, job.priority)

    def on_complete(self, nid: NodeId) -> None:
        """Completion hook: free the owner's window slot and wake paused jobs.

        Waking every paused job on each completion is O(active loops) — a
        handful — and is what lets memory-blocked jobs notice the live tier
        draining without any polling.
        """
        job = self._body_owner.pop(nid, None)
        if job is not None:
            job.in_flight -= 1
        for paused in self._jobs.values():
            paused.wake.set()

    # ── Captures / failure ────────────────────────────────────────────────────

    def hold_captures(self, closure_id: NodeId, capture_ids: tuple[NodeId, ...]) -> None:
        """Pin a closure's captures until its loop has fully expanded."""
        if closure_id not in self.capture_holds:
            self.capture_holds[closure_id] = capture_ids
            for dep in capture_ids:
                self.graph.pin(dep)

    def _release_captures(self, node: NodeSpec) -> None:
        held = self.capture_holds.pop(node.args[1], None)
        if held is not None:
            for dep in held:
                self.graph.release(dep)

    def abort(self, exc: BaseException) -> None:
        """Fail-fast: unblock every paused job so the run can drain and report."""
        self._aborted = exc
        for job in self._jobs.values():
            job.wake.set()

    @property
    def active_jobs(self) -> int:
        return len(self._jobs)

    def shutdown(self) -> None:
        self._reducer.shutdown(wait=False)
