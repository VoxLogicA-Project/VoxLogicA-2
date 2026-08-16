"""Asking for a value, from the UI.

Until now the UI has only ever *watched*: `voxlogica run` computes and the
browser observes. This module is the other direction -- a card's Run button --
and it is the reason a board is more than a picture of a program.

The shape is a **demand set**, not a run queue. What arrives here are node ids,
and node ids are content hashes, so asking twice for the same thing is asking
once. Pressing Run on five overlapping cards is one demand set with one pass over
it, and that falls out of the identity rather than out of any bookkeeping here.

Four decisions, each of which had a plausible alternative:

**One runner, never two engines.** The engine owns process-wide resources -- a
thread pool sized to the machine, a memory governor reasoning about total live
bytes, a store handle. Two concurrent engines would contend for all three, and
the governor would be throttling against half a picture, which is worse than
throttling against none. So a second Run while one is in flight joins the *next*
pass.

**Joining the next pass is nearly free.** The obvious objection to a queue is
that the second Run waits for the first. It does -- and then finds everything the
first computed already in the store, because that is what content addressing is
for. The cost of queueing is the tail of one pass, not the length of two.

**Nothing is cancelled by a new demand.** A run in flight is producing values
that are worth having by definition: they are addressed by content, so no later
edit can invalidate them, only make them unwanted. Cancellation exists for a user
who asks for it, not as a side effect of asking for something else.

**A failure is a result, not an exception.** A demand that fails leaves its node
`failed` in `results.py` with the error attached, and the next pass may well
succeed -- somebody fixed the program. A runner that died on the first failure
would take the rest of the board's demands with it.

See doc/dev/ui-cards.md section 5.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


@dataclass
class Pass:
    """One trip through the demand set, as anything watching sees it."""

    #: Monotone, so a client can tell a new pass from a redelivered one.
    seq: int
    state: str  # "running" | "done" | "failed"
    #: How many nodes were asked for. Not how many will be computed: most of a
    #: demand set is usually already in the store.
    demanded: int = 0
    started: float = field(default_factory=time.time)
    ended: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "state": self.state,
            "demanded": self.demanded,
            "started": self.started,
            "ended": self.ended,
            "error": self.error,
        }


class Compute:
    """The demand set of one UI instance, and the thread that satisfies it."""

    def __init__(self, hub, results, *, execute: Callable[..., Any] | None = None) -> None:
        self._hub = hub
        self._results = results
        #: Injected so this can be tested without an engine, and so the engine
        #: is imported only when something is actually run -- the UI must start
        #: on a machine where importing SimpleITK would be the slowest thing
        #: that has happened all day.
        self._execute = execute or _execute_with_engine
        self._lock = threading.Lock()
        self._wanted: set[str] = set()
        self._source: str = ""
        self._runner: threading.Thread | None = None
        self._seq = 0
        self._pass: Pass | None = None
        self._idle = threading.Event()
        self._idle.set()

    # ------------------------------------------------------------- demanding

    def demand(self, source: str, nodes: Iterable[str]) -> dict[str, Any]:
        """Ask for these nodes, against this document. Returns at once.

        The source travels with the demand because a hash means nothing without
        a plan that contains it: the engine schedules a *work plan*, and the one
        that must be compiled is the document as it read when the user pressed
        Run -- not as it reads by the time the runner gets to it.
        """
        wanted = {node for node in nodes if node}
        if not wanted:
            return self.status()

        with self._lock:
            self._wanted |= wanted
            self._source = source
            # Everything asked for is at least queued, immediately and before
            # any engine has an opinion: a Run that showed nothing until the
            # scheduler got around to it would read as a button that did not
            # work.
            for node in wanted:
                self._results.observe(node, "pending")
            if self._runner is None or not self._runner.is_alive():
                self._idle.clear()
                self._runner = threading.Thread(
                    target=self._drain, name="voxlogica-ui-compute", daemon=True
                )
                self._runner.start()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pass": self._pass.as_dict() if self._pass else None,
                "queued": len(self._wanted),
            }

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until nothing is demanded. For tests and for shutdown."""
        return self._idle.wait(timeout)

    # --------------------------------------------------------------- running

    def _drain(self) -> None:
        """Passes, until the demand set is empty.

        Re-reading the set between passes rather than looping over a snapshot is
        what makes "a Run during a Run joins the next pass" true without a
        second queue to keep in step.
        """
        while True:
            with self._lock:
                if not self._wanted:
                    # Cleared under the lock so `demand` cannot see a runner
                    # that is on its way out and decline to start another one.
                    self._runner = None
                    self._idle.set()
                    finished = True
                else:
                    finished = False
                    nodes = sorted(self._wanted)
                    source = self._source
                    self._wanted = set()
                    self._seq += 1
                    self._pass = Pass(seq=self._seq, state="running", demanded=len(nodes))
            if finished:
                # Outside the lock, always. Publishing reaches every connected
                # client's event loop, and holding a lock across that puts the
                # runner's fate in the hands of whoever is slowest to drain.
                self._publish()
                return
            self._publish()

            try:
                self._execute(source, nodes, self._results.observe)
                ended = "done"
                error = None
                # The pass is over, so nothing it was asked for can still be
                # queued. Most demands do get an event; the ones that do not --
                # already satisfied, folded to a constant, elided inside a fused
                # cone -- would otherwise keep the optimistic `pending` written
                # when they were asked for, on top of the store's own answer,
                # forever. Dropping it lets the store speak again.
                still = [node for node in nodes
                         if self._results.state_of(node)["state"] in ("pending", "computing")]
                if still:
                    self._results.forget(still)
            except BaseException as exc:  # noqa: BLE001 - a failed pass is a result
                logger.debug("compute pass failed", exc_info=True)
                ended = "failed"
                error = f"{type(exc).__name__}: {exc}"
                # The demand failed as a whole -- a plan that would not compile,
                # most likely. Individual node failures have already been
                # reported by the observer; this covers the ones that never got
                # that far, so nothing is left claiming to be queued forever.
                for node in nodes:
                    if self._results.state_of(node)["state"] in ("pending", "computing"):
                        self._results.observe(node, "failed", error=error)

            with self._lock:
                if self._pass is not None:
                    self._pass.state = ended
                    self._pass.error = error
                    self._pass.ended = time.time()
            self._publish()

    def _publish(self) -> None:
        if self._hub is None:
            return
        # Sticky: a browser that connects mid-pass is told there is one, which
        # is the difference between a quiet UI and a UI that looks broken.
        self._hub.publish({"type": "compute", **self.status()}, sticky_key="compute")


def _execute_with_engine(source: str, nodes: list[str], observe) -> None:
    """Compile the document and compute those nodes, reporting as it goes.

    Imported here rather than at module scope: the engine pulls in SimpleITK and
    numba, and a UI that paid for that at startup would be a UI that takes ten
    seconds to show an empty board.
    """
    from voxlogica.execution import ExecutionEngine
    from voxlogica.main import build_workplan
    from voxlogica.storage import get_storage

    _syntax, workplan = build_workplan(source, source_name="<workspace>")

    # A demand is a goal. The language already has the right kind of one:
    # `value` materialises a node and does nothing else, unlike `print` and
    # `save`, which are effects. Adding one per demanded node is how "show me
    # this" becomes something the scheduler can be given.
    declared = {goal.id for goal in workplan.goals}
    wanted: list[str] = []
    for node in nodes:
        if node not in workplan.nodes:
            # Asked for against a document that has since changed, or never
            # contained it. Not an error: the node simply is not in this plan,
            # and saying so by leaving it alone is better than failing the pass
            # for every other card that asked.
            continue
        if node not in declared:
            workplan.add_goal("value", node, node)
        wanted.append(node)

    if not wanted:
        return

    # Only what was asked for. Running the document's own goals as well would
    # fire its `save`s -- writing files -- every time somebody pressed Run on an
    # unrelated card, which is not what a Run button can be allowed to mean.
    engine = ExecutionEngine(storage_backend=get_storage(), observe=observe)
    engine.execute_workplan(workplan, goals=wanted)
