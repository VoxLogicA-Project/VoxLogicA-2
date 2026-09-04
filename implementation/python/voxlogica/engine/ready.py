"""The ready queue: priority dispatch, memory parking, and run-completion.

A plain binary heap plus one wake event — cheaper than asyncio.PriorityQueue
(which pays for per-getter futures and its own join bookkeeping) and sufficient
because all producers and consumers live on the single event loop.

Orderings and tiers:
- Entries are ``(-priority, -seq, nid)``: highest priority first; equal
  priority drains newest-first (LIFO). LIFO makes evaluation depth-first — a
  freshly produced value is consumed by its dependent before more siblings are
  produced, so intermediates die young and the live tier stays small under
  wide fan-out (Cilk's work-first heuristic, applied to memory).
- ``park``/``unpark``: when the live tier is over budget, ready nodes are held
  in a side heap instead of widening the frontier. PROGRESS FLOOR: parked work
  is always admitted when the main heap would otherwise starve the workers, so
  parking can never deadlock the run — at worst the soft memory budget is
  briefly exceeded.

Run completion (``outstanding``): every admitted-but-unfinished unit of work —
a queued/running node or an active expansion job — holds one count. The run is
over when the count reaches zero. This replaces ``Queue.join()`` and, unlike
it, cannot report "done" between a worker finishing a loop node and the loop's
expansion admitting the bodies (both hold a unit through the gap).
"""

from __future__ import annotations

import asyncio
import heapq
import itertools

from voxlogica.lazy.ir import NodeId


class ReadyQueue:
    """Priority heap + parked tier + outstanding-unit accounting."""

    def __init__(self):
        self._heap: list[tuple[int, int, NodeId]] = []
        self._parked: list[tuple[int, int, NodeId]] = []
        self._seq = itertools.count()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self.outstanding = 0

    # ── Units (run-completion accounting) ────────────────────────────────────

    def begin_unit(self) -> None:
        """One more admitted-but-unfinished unit (queued node / expansion job)."""
        self.outstanding += 1
        self._idle.clear()

    def end_unit(self) -> None:
        """A unit finished; the run is complete when none remain."""
        self.outstanding -= 1
        if self.outstanding <= 0:
            self._idle.set()

    async def wait_idle(self) -> None:
        """Resolve when every admitted unit has finished."""
        await self._idle.wait()

    # ── Queue ────────────────────────────────────────────────────────────────

    def push(self, nid: NodeId, priority: int) -> None:
        """Offer a ready node to the workers (counts as one unit)."""
        self.begin_unit()
        heapq.heappush(self._heap, (-priority, -next(self._seq), nid))
        self._wake.set()

    async def pop(self) -> NodeId:
        """Return the highest-priority ready node; wait if none."""
        while not self._heap:
            self._wake.clear()
            await self._wake.wait()
        return heapq.heappop(self._heap)[2]

    def qsize(self) -> int:
        return len(self._heap)

    # ── Memory parking ───────────────────────────────────────────────────────

    def park(self, nid: NodeId, priority: int) -> None:
        """Hold a ready node aside while the live tier is over budget."""
        self.begin_unit()
        heapq.heappush(self._parked, (-priority, -next(self._seq), nid))

    def unpark(self, over_budget: bool, starving: bool) -> None:
        """Admit parked nodes: freely under budget, one at a time if starving.

        The ``starving`` arm is the progress floor — invoked when the main heap
        cannot feed the workers, it admits regardless of the budget so that
        memory pressure can slow the run but never wedge it.
        """
        while self._parked and (not over_budget or starving):
            entry = heapq.heappop(self._parked)
            heapq.heappush(self._heap, entry)
            self._wake.set()
            if starving and over_budget:
                break  # floor: just enough to keep moving

    @property
    def parked_count(self) -> int:
        return len(self._parked)
