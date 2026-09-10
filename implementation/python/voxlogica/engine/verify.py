"""The scheduler's progress invariant, written down and checked.

WHY THIS MODULE EXISTS

Eleven defects were fixed in this engine on 2026-09-10, and ten of them are one
class: *a value that only an expansion can produce, reached by a path that
cannot produce it*. They surfaced as an unbounded spill queue, a dict mutated
under iteration, an ignored refused wait, a silently dead worker, a recompute
that dropped its own argument, `NeedsExpansion` on a pool thread, a run
declaring itself finished while a goal was unresolvable -- seven different
symptoms, one missing rule. Each fix was local, and the failure MOVED rather
than went: 37 s, then 4 min 10 s, then 27 s, then 84 minutes.

A failure that relocates under repair is a failure whose invariant has never
been stated. So this module states it, once, and checks it -- because the
alternative is to keep reading tracebacks and guessing which of six subsystems
was wrong.

THE INVARIANT

The engine's whole job is to drive a frontier to empty. A node waits when its
dependencies are not available, and something must be producing what it waits
for. Precisely:

    (P) PROGRESS. For every node n on the frontier, either n is available to
        run now (pending(n) == 0), or at least one node it is waiting for is
        being produced: in flight, in the ready heap, in the parked tier, or
        owned by an active expansion job.

    (T) TERMINATION. When the run-completion counter reaches zero -- which is
        what `wait_idle` joins on, and therefore what makes `run()` return --
        the frontier must be empty.

    (V) ANSWERABILITY. Every goal that has settled must have a value that can
        be resolved to the bottom without further scheduling: no handle inside
        it may name a node that is neither resident, nor recomputable by a
        kernel, nor already scheduled for expansion.

(P) is the one that catches the class. Every instance above ends with the same
census -- and this is the measured one, from the twelfth instance, a sixty-case
probe that died after 84 minutes and 2,324,411 completions:

    ready = 0, in_flight = 0, parked = 0, jobs = 0, frontier = 248
    stalled: 59eeaf2ea40a op=default.for_loop deps=2 pending=1 value=False
             28edf1da234a op=default.for_loop deps=2 pending=1 value=False

Two loop nodes each waiting for one dependency that nobody is producing. Under
(P) that state is detected the moment it arises, at the node that caused it,
instead of hours later at whichever goal happened to need it.

WHAT IT COSTS, AND WHY THAT IS THE WHOLE DESIGN

Off by default and free when off: nothing on any dispatch path consults it. On,
it runs in two modes, because the useful check is O(frontier) and the frontier
reaches hundreds of thousands of nodes:

  * `drain` -- the full check, when the run believes it is finished. That is
    exactly when (T) and (V) are meaningful, and paying O(frontier) once per
    run is free.
  * `periodic` -- (P) on a bounded SAMPLE of the frontier, every N completions.
    A violation of (P) is persistent: once a node waits on nothing, it waits on
    nothing for the rest of the run, so a sample finds it with probability
    rising toward one and the cost stays constant. This is the same rule
    `measure.py` obeys -- bounded per-sample cost, or the instrument becomes
    the thing being measured.

Reporting, not raising, is the default. A checker that aborts a fourteen-hour
sweep on a violation that the run would have survived costs more than it
finds; `--verify=strict` raises for the test suite and for CI, where a
violation must fail the build.
"""

from __future__ import annotations

import random
import sys
from typing import Any, Iterable

from voxlogica.lazy.ir import NodeId

#: Frontier nodes examined per periodic check. Bounded for the same reason
#: `measure.py` bounds its per-sample reads: the frontier is unbounded and the
#: instrument's cost must not be.
_SAMPLE = 512

#: Completions between periodic checks. At ~500 node/s this is about one check
#: every twenty seconds.
_EVERY = 10_000


class Violation:
    """One counterexample to one clause, with the state that proves it."""

    __slots__ = ("clause", "node", "detail")

    def __init__(self, clause: str, node: NodeId | None, detail: str) -> None:
        self.clause, self.node, self.detail = clause, node, detail

    def __str__(self) -> str:
        where = f" at {self.node[:12]}" if self.node else ""
        return f"[{self.clause}]{where} {self.detail}"


class Verifier:
    """Checks the invariant against a live engine. Holds no engine state.

    Deliberately reads the engine rather than being told: a checker that has to
    be kept informed is a second copy of the bookkeeping it exists to audit,
    and the two would drift. Everything here is a read of state the scheduler
    maintains anyway.
    """

    def __init__(self, engine: Any, *, strict: bool = False,
                 sample: int = _SAMPLE, every: int = _EVERY) -> None:
        self._engine = engine
        self._strict = strict
        self._sample = max(1, int(sample))
        self._every = max(1, int(every))
        self._next_at = self._every
        self.checks = 0
        self.violations: list[Violation] = []
        #: Reported once per (clause, node): a persistent violation would
        #: otherwise print on every periodic check for the rest of the run.
        self._seen: set[tuple[str, str]] = set()

    # ── the clauses ──────────────────────────────────────────────────────────

    def _producer_of(self, dep: NodeId, ready_set: set[NodeId],
                     job_owned: set[NodeId]) -> str | None:
        """Why `dep` will arrive, or None if nothing is producing it."""
        engine, table = self._engine, self._engine.table
        if dep in table.values or dep in table.completed:
            return "already available"
        if table.is_running(dep):
            return "in flight"
        if dep in ready_set:
            return "queued"
        if dep in job_owned:
            return "expansion job"
        if dep in getattr(engine, "_alias", ()):  # forwarded from its target
            return "aliased"
        try:
            if table.persisted(dep):
                return "on disk"
        except Exception:                                       # noqa: BLE001
            pass
        return None

    def check_progress(self, nodes: Iterable[NodeId], ready_set: set[NodeId],
                       job_owned: set[NodeId]) -> list[Violation]:
        """(P) on the given frontier nodes."""
        engine, graph, table = self._engine, self._engine.graph, self._engine.table
        out: list[Violation] = []
        for nid in nodes:
            if graph.pending.get(nid, 0) == 0:
                continue                    # runnable now: nothing to wait for
            unmet = [d for d in graph.deps(nid)
                     if d not in table.values and d not in table.completed]
            # A wait can also be on a node that is NOT a graph dependency --
            # `await_one` is used for loop splicing and for handle references --
            # so an empty `unmet` here is not itself a violation; what matters
            # is whether ANYTHING it could be waiting for has a producer.
            producers = {d: self._producer_of(d, ready_set, job_owned) for d in unmet}
            if unmet and all(v is None for v in producers.values()):
                node = table.nodes.get(nid)
                out.append(Violation(
                    "P", nid,
                    f"op={getattr(node, 'operator', None)!r} "
                    f"pending={graph.pending.get(nid)} "
                    f"deps={len(graph.deps(nid))} unmet={len(unmet)} "
                    f"and nothing is producing any of them "
                    f"({', '.join(d[:12] for d in list(unmet)[:4])})"))
            elif not unmet and graph.pending.get(nid, 0) > 0:
                # Waiting with every graph dependency met. Legitimate while a
                # splice or a handle reference is outstanding, so it is only a
                # violation when nothing at all is in motion -- which the drain
                # check establishes separately.
                out.append(Violation(
                    "P?", nid,
                    f"op={getattr(table.nodes.get(nid), 'operator', None)!r} "
                    f"pending={graph.pending.get(nid)} with all graph deps met "
                    f"-- waiting on a splice or a handle reference"))
        return out

    def check_termination(self) -> list[Violation]:
        """(T) the frontier must be empty when the unit count reaches zero."""
        engine, graph = self._engine, self._engine.graph
        out: list[Violation] = []
        outstanding = getattr(engine.ready, "outstanding", 0)
        frontier = len(graph.incomplete)
        if outstanding <= 0 and frontier:
            out.append(Violation(
                "T", None,
                f"the run is complete by its own accounting (outstanding="
                f"{outstanding}) but {frontier} node(s) are still on the "
                f"frontier"))
        if outstanding > 0 and not frontier and engine._in_flight == 0 \
                and engine.ready.qsize() == 0 \
                and getattr(engine.ready, "parked_count", 0) == 0 \
                and getattr(engine.admission, "active_jobs", 0) == 0:
            units = dict(getattr(engine.ready, "units", {}) or {})
            out.append(Violation(
                "T", None,
                f"{outstanding} run-completion unit(s) are held by nothing: "
                f"frontier empty, nothing queued, parked, running or "
                f"expanding. ledger={units}"))
        return out

    def check_answerability(self) -> list[Violation]:
        """(V) every settled goal must resolve to the bottom.

        Uses the engine's own reference resolution, so what it verifies is
        exactly what a caller will do -- and catches the case that killed a
        run after every goal had already been reported open.
        """
        engine, table = self._engine, self._engine.table
        out: list[Violation] = []
        try:
            from voxlogica.engine.evaluation import NeedsExpansion
            from voxlogica.handles import resolve_deep
        except Exception:                                       # noqa: BLE001
            return out
        for goal in list(getattr(engine, "_goals", ())):
            value = table.values.get(goal)
            if value is None:
                continue
            missing: list[str] = []

            def probe(ref: NodeId, _m=missing) -> Any:
                try:
                    return engine._resolve_reference(ref)
                except NeedsExpansion as needed:
                    _m.append(needed.node_id)
                    return None
                except Exception:                               # noqa: BLE001
                    return None
            try:
                resolve_deep(value, probe)
            except Exception:                                   # noqa: BLE001
                continue
            for ref in missing:
                out.append(Violation(
                    "V", ref,
                    f"goal {goal[:12]} has settled but names a node that only "
                    f"an expansion can produce, and none is scheduled"))
        return out

    # ── entry points ─────────────────────────────────────────────────────────

    def _sets(self) -> tuple[set[NodeId], set[NodeId]]:
        engine = self._engine
        ready_set: set[NodeId] = set()
        try:                        # the heap's third element is the node id
            ready_set = {e[2] for e in engine.ready._heap}
            ready_set |= {e[2] for e in engine.ready._parked}
        except Exception:                                       # noqa: BLE001
            pass
        job_owned: set[NodeId] = set()
        try:
            jobs = getattr(engine.admission, "_jobs", {}) or {}
            job_owned = set(jobs)
            for job in list(jobs.values()):
                job_owned |= set(getattr(job, "staged", ()) or ())
        except Exception:                                       # noqa: BLE001
            pass
        return ready_set, job_owned

    def on_completion(self, completed: int) -> None:
        """Called from the completion path. One integer compare when not due."""
        if completed < self._next_at:
            return
        self._next_at = completed + self._every
        frontier = list(self._engine.graph.incomplete)
        if len(frontier) > self._sample:
            frontier = random.sample(frontier, self._sample)
        ready_set, job_owned = self._sets()
        self._report(self.check_progress(frontier, ready_set, job_owned)
                     + self.check_termination(), where="periodic")

    def at_drain(self) -> list[Violation]:
        """The full check, when the run believes it is finished."""
        ready_set, job_owned = self._sets()
        found = (self.check_termination()
                 + self.check_progress(list(self._engine.graph.incomplete),
                                       ready_set, job_owned)
                 + self.check_answerability())
        self._report(found, where="drain")
        return found

    def _report(self, found: list[Violation], *, where: str) -> None:
        self.checks += 1
        fresh = [v for v in found
                 if (v.clause, v.node or "") not in self._seen]
        for v in fresh:
            self._seen.add((v.clause, v.node or ""))
        self.violations.extend(fresh)
        if not fresh:
            return
        for v in fresh[:8]:
            print(f"[verify:{where}] {v}", file=sys.stderr, flush=True)
        if len(fresh) > 8:
            print(f"[verify:{where}] ... and {len(fresh) - 8} more",
                  file=sys.stderr, flush=True)
        hard = [v for v in fresh if v.clause in ("P", "T", "V")]
        if self._strict and hard:
            raise SchedulerInvariantViolated(hard)

    def summary(self) -> dict[str, Any]:
        """For the measurement report: what was checked and what was found."""
        by_clause: dict[str, int] = {}
        for v in self.violations:
            by_clause[v.clause] = by_clause.get(v.clause, 0) + 1
        return {
            "checks": self.checks,
            "violations": len(self.violations),
            "by_clause": by_clause,
            "first": [str(v) for v in self.violations[:8]],
            "clauses": {
                "P": "a frontier node waits only for something being produced",
                "T": "the frontier is empty exactly when no units remain",
                "V": "a settled goal resolves to the bottom without scheduling",
            },
        }


class SchedulerInvariantViolated(AssertionError):
    """Raised under --verify=strict. Carries the counterexamples."""

    def __init__(self, violations: list[Violation]) -> None:
        super().__init__("; ".join(str(v) for v in violations[:4])
                         + (f" (+{len(violations) - 4} more)"
                            if len(violations) > 4 else ""))
        self.violations = violations
