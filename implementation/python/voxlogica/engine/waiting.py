"""The wait/claim protocol: who is waiting for what, and what may be dropped.

WHY THIS IS ITS OWN MODULE
--------------------------
Twelve consecutive attempts to change how the engine rebuilds an evicted value
each failed differently, and every one of them failed inside this protocol
rather than in the code that called it. The failures were not subtle bugs so
much as three ideas sharing one set of fields:

- **the wait count was assigned as well as decremented.** `await_one` SET
  `pending[n] = 1` while `register` set it to the number of unmet inputs and
  `on_complete` decremented it. A node asked to wait for a second input
  therefore ended up waiting for one, fired on the first arrival, asked again,
  and lost the wakeup for anything that had completed in between. Measured as
  2,950 nodes with `pending=1` on inputs that never arrived, an empty queue,
  and "engine finished with an unresolved goal" -- and only under load.

- **"computed" and "its value is here" were one state.** `completed`
  membership meant both, so a node whose value had been evicted could not be
  scheduled again, which is why rebuilds had to happen outside the scheduler in
  the first place -- on the event loop, blocking every worker: 10.86 s of
  kernel in a 33 s run.

- **eviction protection was three fields honoured by different paths.**
  `protected`, `pinned` and `consumers` each stopped some evictions and not
  others: a dispatch pin held off the pressure sweeps but not the refcount, so
  a value could vanish between being made resident and being read.

So this module owns exactly those three things, with one rule each. It has no
dependency on the engine, the table or the graph -- it is a data structure
about ids -- which is what makes the invariants testable on their own.

THE PROTOCOL
------------
Each node id has, independently:

- a WAIT COUNT: how many of its inputs are not yet available. Only ever
  `add_wait` (+1) and `arrived` (-1). Never assigned. A node whose count is
  zero is not waiting.
- a PHASE: `computed` or not. Orthogonal to whether its value is resident,
  which this module does not track and does not need to.
- a CLAIM COUNT: how many nodes are relying on its value right now. Only ever
  `claim` (+1) and `unclaim` (-1). `may_drop` is false while it is positive,
  and it is the ONLY question an eviction path has to ask.

Invariants, each enforced by an assertion or a test:

1. `add_wait(n, dep)` then `arrived(dep)` fires `n` exactly once, whatever the
   order and however many inputs `n` waits for.
2. A wakeup is never lost: `arrived` on an input nobody waits for is a no-op,
   and `add_wait` after the arrival is refused rather than silently pending
   forever -- the caller is told, and can proceed.
3. `may_drop(id)` is false while any claim is outstanding, and every claim is
   released exactly once.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable


class WaitLedger:
    """Wait counts, phases and claims for node ids. No I/O, no engine."""

    __slots__ = ("_waits", "_waiters", "_computed", "_claims", "_arrived")

    def __init__(self) -> None:
        #: id -> how many of its inputs are still missing
        self._waits: dict[str, int] = defaultdict(int)
        #: input id -> ids waiting on it
        self._waiters: dict[str, list[str]] = defaultdict(list)
        #: ids that have been computed at least once
        self._computed: set[str] = set()
        #: id -> how many nodes rely on its value right now
        self._claims: dict[str, int] = defaultdict(int)
        #: inputs whose arrival has already been announced
        self._arrived: set[str] = set()

    # ── waiting ─────────────────────────────────────────────────────────────

    def add_wait(self, node: str, dep: str) -> bool:
        """`node` waits for one more input. False if `dep` is already here.

        Returning False rather than recording a wait is invariant 2: a wait
        registered after the arrival would never be answered, and the caller
        needs to know now so it can look again instead of parking forever.
        """
        if dep in self._arrived:
            return False
        self._waits[node] += 1
        self._waiters[dep].append(node)
        return True

    def arrived(self, dep: str) -> list[str]:
        """`dep`'s value exists. Returns the ids whose last wait this was."""
        self._arrived.add(dep)
        fired: list[str] = []
        for node in self._waiters.pop(dep, ()):
            remaining = self._waits.get(node, 0) - 1
            if remaining <= 0:
                self._waits.pop(node, None)
                fired.append(node)
            else:
                self._waits[node] = remaining
        return fired

    def gone(self, dep: str) -> None:
        """`dep`'s value no longer exists, so a later wait on it is legitimate."""
        self._arrived.discard(dep)

    def waiting_for(self, node: str) -> int:
        """How many inputs `node` is still missing. Zero means not waiting."""
        return self._waits.get(node, 0)

    def is_waiting(self, node: str) -> bool:
        return self._waits.get(node, 0) > 0

    # ── phase ───────────────────────────────────────────────────────────────

    def mark_computed(self, node: str) -> None:
        self._computed.add(node)

    def is_computed(self, node: str) -> bool:
        """Whether it has ever been computed. Says NOTHING about residency."""
        return node in self._computed

    # ── claims ──────────────────────────────────────────────────────────────

    def claim(self, deps: Iterable[str]) -> tuple[str, ...]:
        """Rely on these values until the returned token is unclaimed."""
        held = tuple(deps)
        for dep in held:
            self._claims[dep] += 1
        return held

    def unclaim(self, held: Iterable[str]) -> None:
        for dep in held:
            remaining = self._claims.get(dep, 0) - 1
            if remaining <= 0:
                self._claims.pop(dep, None)
            else:
                self._claims[dep] = remaining

    def may_drop(self, node: str) -> bool:
        """The ONLY question an eviction path asks."""
        return self._claims.get(node, 0) <= 0

    def claims_on(self, node: str) -> int:
        return self._claims.get(node, 0)

    # ── reporting ───────────────────────────────────────────────────────────

    def stuck(self) -> dict[str, int]:
        """Ids still waiting, with their counts: the report a hang needs."""
        return {node: n for node, n in self._waits.items() if n > 0}
