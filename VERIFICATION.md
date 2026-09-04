# VERIFICATION.md — what can go wrong in the engine, and what formal methods could establish

Status: proposal, for review. Written 2026-08-22 against `f66d377`.

This document is about the **execution engine** (`implementation/python/voxlogica/engine/`):
a persistent, content-addressed, priority-scheduled evaluator with a bounded
memory budget, an asynchronous writer, and a two-tier cache. It is the component
whose failures are hardest to diagnose, because its characteristic bad outcome is
not a wrong answer but *no answer* — a run that stops making progress, or one
that is killed for using too much memory.

An incident report for one such failure is in
[`doc/dev/engine-verification-strategy.md`](doc/dev/engine-verification-strategy.md),
along with the engineering changes proposed in response. **This document is the
formal-methods companion**: what the failure *classes* are, which properties
would exclude each, which techniques can establish those properties, and — the
question that decides whether any of it is worth doing — **how a model would be
tied to the code that runs**.

---

## 1. Summary

The engine's job is to evaluate a DAG of expressions under a memory bound, using
a disk cache as spill space, with N worker threads and an asynchronous writer.
Its correctness argument is unusually explicit: every value is regenerable from
its lineage, so nothing is ever *wrong*; the risk is entirely in **progress** and
**resource bounds**.

That shifts the verification problem away from functional correctness — where
content addressing and determinism already do most of the work — and onto three
questions:

1. **Does it always finish?** Not "is the answer right", but: can the engine reach
   a state where work is outstanding, memory is at its budget, and no reclaim
   action frees anything? This has never been proven, and one production incident
   is consistent with it having happened.
2. **Does it stay inside its budget?** Historically no: code comments record
   36.6 GB resident against a 25 GB budget, and an unwritten backlog growing from
   0.5 to 10.3 GB in ten seconds. Each of those was fixed by adding a valve. There
   is no argument that the current set of valves is sufficient.
3. **Would we know?** Today, largely not. Two different counters report progress
   and can disagree; the stall watchdog cannot fire while any kernel is in flight;
   no check observes the completion *rate*; and the invariants that would catch
   the rest are written in comments rather than in code.

The techniques that fit are, in increasing cost: **executable invariants** over a
snapshot of engine state (days); **property-based and model-based testing** on a
deterministic harness with fake kernels (a week, and it would have caught the most
recent regression in under a second); **model checking** an abstraction of the
resource protocol in TLA+ or mCRL2 (weeks, and the only technique that answers
question 1 for all schedules rather than for the ones we ran); plus two cheap
additions — **Alloy** for the structural invariants, whose counterexamples read
best, and **CrossHair** for symbolic execution of the invariant predicates, which
verifies the code rather than a model of it.

The recommendation on binding models to code is **not** code generation and
**not** model extraction. It is to *restructure so that the protocol is a small,
pure, deterministic core* — a state machine over integers and sets, with no I/O
and no threads — which can then be (a) executed by the real engine, (b)
model-checked as an abstraction, and (c) monitored at runtime by predicates
derived from the same specification. Everything else is glue. §7 argues this at
length, and §8 lists the ways it can go wrong, including the two most likely:
verifying a model that has drifted from the code, and mistaking a green model
check for a proof about the system.

---

## 2. At a glance

Failure classes, the property that excludes each, and the technique that can
establish it. Terminology is defined in §10.

| # | Failure class | Observed? | Property class | Best technique | Binding to code |
|---|---|---|---|---|---|
| F1 | Deadlock (circular wait) | No | Safety (reachability of a blocked state) | Model checking | Abstraction + conformance tests |
| F2 | Livelock / divergence | **Suspected** (incident) | Liveness under fairness | mCRL2 (divergence is primitive) or TLA+ | Abstraction + conformance tests |
| F3 | Starvation of one node or query | Plausible by inspection | Liveness (per-element progress) | Model checking with fairness | Abstraction |
| F4 | Memory-bound violation | **Yes**, historically | Safety (invariant `accounted ≤ hard`) | Executable invariant + property tests | Direct |
| F5 | Unbounded write backlog | **Yes**, historically | Safety (invariant on queue bytes) | Executable invariant | Direct |
| F6 | Stranded value (evicted, not recomputable, not durable) | Guarded | Safety (invariant) | Executable invariant + Alloy | Direct / structural |
| F7 | Double computation | Guarded (`DoubleComputationError`) | Safety | Property tests | Direct |
| F8 | Use-after-evict race | **Yes**, historically (`KeyError` in executor) | Safety under interleaving | Model checking of the pin protocol | Abstraction |
| F9 | Recompute thrash (evict⇄recompute oscillation) | **Yes**, historically (246,318 recomputes) | Liveness + efficiency | Property tests measuring recompute counts | Direct |
| F10 | Silent write loss | **Yes** (issue #39) | Safety (durability) | Fault-injection tests | Direct |
| F11 | Silent policy no-op | **Yes** (a `--sparse-cache` filter that did nothing) | Refinement (code ≢ intent) | Contracts + property tests | Direct |
| F12 | Progress-reporting incoherence | **Yes** (issue #41) | Consistency of observers | Type/API discipline | Direct |
| F13 | Watchdog blindness | **Yes** (issue #43) | Real-time property | UPPAAL (timed automata) | Abstraction |
| F14 | Crash: OOM kill, SIGSEGV in a kernel | **Yes**, historically | Safety (resource) / out of scope for FM | Torture testing | Direct |
| F15 | Data race under free threading | Unaudited | Safety under interleaving | Targeted review; race detection | Direct |

Two rows deserve emphasis, because they are the reason to consider formal
methods at all rather than more tests:

- **F2** is the only class where testing is structurally weak. A livelock is an
  infinite execution that makes no progress; no finite test observes one, only
  its symptom (a timeout), and the symptom is indistinguishable from slowness.
- **F8** is the class where testing is *possible but unreliable*: it depends on a
  specific interleaving of a pool thread's read against the event loop's eviction
  sweep. Tests find such bugs by luck; model checking finds them by enumeration.

---

## 3. The engine in one page

Enough of the design to read the rest. File references are to
`implementation/python/voxlogica/`.

**Values and identity.** Every node is identified by a content hash of its
expression. A value is therefore *regenerable*: it can be recomputed from its
lineage at any time. This is stated in `engine/liveness.py` as the reason liveness
is "an eviction *preference* — never a correctness gate".

**Two tiers.** A RAM *live tier* (`NodeTable.values`, `engine/node_table.py:109`)
and a persistent tier (SQLite metadata plus payload files, `storage.py`). The
persistent tier is not merely a cache: it is the engine's **spill space**. RAM may
drop a value only if it can be recovered — either from disk, or by recomputing it.

**Accounting.** `NodeTable.accounted_bytes` (`node_table.py:257`) is live tier +
unwritten persist backlog + pooled buffers. The `MemoryGovernor`
(`engine/governor.py`) samples RSS and derives a **soft budget** (parking and
reclaim trigger here) and a **hard ceiling** (admission refuses here). It may only
shrink the configured budget, never raise it.

**Admission.** `engine/admission.py` admits loop bodies demand-driven, on
ready-queue depth rather than a fixed window, refusing above the hard ceiling —
with one exemption: a *true wedge* (nothing running, nothing ready) admits one
unit to guarantee progress.

**Reclaim.** `ComputationEngine._reclaim_memory` (`engine/core.py:724`), on every
worker turn, bounded to `_EVICT_SWEEP = 256` candidates per pass:

- **PASS 0** drops *ownerless* values (zero remaining consumers: no write needed,
  no future read). Also triggered when ownerless bytes exceed
  `_ownerless_share = 0.25` of the budget, because speculative cache once grew to
  23.1 GB of a 25 GB budget and evicted certain-read values to make room.
- **PASS 1** evicts values whose write has landed.
- **PASS 2**, per candidate, in order: evict if durable; else **drop and
  recompute** if `compute_ms < sacrifice_ms` and the node is recomputable
  (`_recomputable`, `core.py:885` — loop and sequence nodes are not, because the
  executor cannot re-run them); else **force a write** via `NodeTable.spill`
  (`node_table.py:423`); else requeue for a later sweep.

`sacrifice_ms` ramps with RSS pressure (`governor.py:180`): near the ceiling, the
alternative to sacrificing a 200 ms recompute is being killed and losing every
undurable byte at once.

**The writer.** `engine/persist.py`, several writer threads behind a queue with a
byte budget (512 MB default, `node_table.py:53`). `spill()` respects that budget
and returns `False` when saturated, deliberately: an earlier version that ignored
it grew the backlog from 0.5 to 10.3 GB in ten seconds while the live copies it
was meant to free stayed resident. As its docstring puts it, "a queued write is
not reclaimed memory; it is the same memory, twice."

**In-flight protection.** `_dispatch_pins` (`core.py:~333`) pins a dispatch's
resident dependencies for the exact span of that dispatch, because reclaim may
otherwise evict a value a pool thread is reading right now — which manifested as
a `KeyError` inside the executor.

**Liveness watchdog.** `_join_with_watchdog` (`core.py:451`) raises if no node
completes for `VOXLOGICA_STALL_TIMEOUT_S` (180) **and** nothing is running, ready,
or admitted; and warns, without raising, if `VOXLOGICA_HANG_TIMEOUT_S` (3600)
passes with a kernel still in flight.

**Threading.** Free-threaded CPython (`PYTHON_GIL=0`) with a worker pool, an event
loop, several writer threads, and an expansion thread. Several data structures are
shared without locks, justified in comments by single dict/set operations being
atomic.

---

## 4. The failure classes in depth

Each subsection states the mechanism, whether it has been observed, why it is hard
to catch by testing, and the property that would exclude it.

### F1 — Deadlock

**Mechanism.** A cycle of waiting: A holds what B needs and vice versa. In this
engine the candidates are not mutexes — the scheduling path is largely lock-free —
but *resource* cycles: a worker cannot finish because it cannot land its output,
and memory cannot be freed until workers finish.

**Observed?** No true circular-wait deadlock is on record. The engine's
`deadlocked` predicate exists precisely to detect the resource version, and it has
fired historically (the "0%-CPU freeze" referenced in the watchdog docstring).

**Why testing is weak.** The interleavings that produce it are rare and
load-dependent, and a test that does not reproduce it proves nothing.

**Property.** Safety: no reachable state has outstanding work, nothing running,
nothing ready, and nothing admissible. Expressible as an invariant and checkable
exhaustively on a small model.

### F2 — Livelock and divergence

**Mechanism.** The engine executes forever without progressing. The concrete
candidate here: reclaim sweeps run on every worker turn; each sweep scans up to
256 candidates; if every candidate is non-durable, not sacrificeable (its
`compute_ms` exceeds `sacrifice_ms`), and not spillable (the writer is saturated),
then every candidate is requeued and the sweep frees nothing. Meanwhile admission
refuses new work because accounted bytes are at budget, and the values that would
free memory are exactly the ones no exit applies to.

**Observed? Yes, and the mechanism is now measured — but it is not the one
above.** A second occurrence, on a 31-case batch, was instrumented while live:

| observation | value |
|---|---|
| threads in state `R` | **1**, and its tid equals the pid — the asyncio **event loop** |
| that thread's CPU | utime 76609 → 77563 jiffies in 10 s wall = **95% continuous** |
| other threads | **71 sleeping**, none in `D` |
| `_in_flight` | 15 coroutines suspended mid-`await` |
| ready queue | **2041** units, parked |
| retired nodes | frozen |
| static plan size | **782,925 nodes** for 31 cases |

So the engine is not short of memory exits: **the event loop is executing
synchronous work and never returning to the scheduler**. Workers sleep because
nothing dispatches to them; the 15 suspended coroutines are never resumed;
admission never runs, so 2041 ready units stay parked.

This also explains the watchdog's silence *definitively*, and more simply than
§4-F13 does: `_join_with_watchdog` is a coroutine **on that same loop**. If the
loop does not yield, the watchdog does not run at all. Zero `[watchdog]` lines in
five hours was never evidence that completions continued — it was the guard being
unable to execute.

The plan size is the quantitative hint: at 783k statically-reduced nodes for one
batch (and 9.3M for the single-process run that wedged first, harder, and
sooner), **any synchronous O(plan) operation on the loop costs minutes**.
Candidates on the loop path include subgraph scheduling and priority raising.

**Still unknown: which function.** The failure is not deterministic — the same
batch, same store, wedged once and completed the second time — and no stack could
be obtained (§6.7). Naming the function is what turns this from a class into a
defect.

**Why testing is structurally weak.** A livelock is an infinite execution. Tests
observe timeouts, and a timeout is indistinguishable from slowness — which is
exactly the ambiguity that made the incident undiagnosable.

**Property.** Liveness under fairness: *always eventually*, either a node
completes or the run is finished. In mCRL2 this is a **divergence** check, which
the toolset provides directly. In TLA+ it is a temporal formula with weak fairness
on the workers, the writer, and the sweep.

### F3 — Starvation

**Mechanism.** Global progress continues, but one element never advances. Two
candidates: (a) a candidate repeatedly requeued in PASS 2 while others are
serviced — the queue is a `deque` and the sweep window is 256, so an unlucky
candidate can sit behind a long tail indefinitely; (b) a low-priority query whose
ancestors are never lifted (issue #26).

**Observed?** A related effect was measured and fixed: with a single shared FIFO,
values already durable "sat tens of thousands of entries behind the 256-per-sweep
scan window", giving 110,495 spills against 1,972 evictions. That is starvation of
a *pass*, cured by splitting the queue.

**Property.** Liveness per element, under fairness: every candidate that remains a
candidate is eventually serviced. Requires strong fairness on the queue discipline
to be provable, which is itself worth knowing — if the property needs strong
fairness, the implementation needs an explicit anti-starvation rule.

### F4 — Memory-bound violation

**Mechanism.** Accounted bytes exceed the hard ceiling, and the OS kills the
process (exit 247 = SIGKILL under the project's launcher).

**Observed?** Yes, repeatedly, and each occurrence produced a valve: the
`_ownerless_share` cap, the sacrifice ramp, the `spill` backlog respect. Comments
record "36.6 GB against a 25 GB budget" and "grew past 42 GB until killed".

**Property.** Safety invariant `accounted ≤ hard` at every scheduling point. This
is the easiest property in the document to state and the one most cheaply checked
by an executable invariant plus property tests — no model checker required.

### F5 — Unbounded write backlog

**Mechanism.** Values are queued for writing faster than the writer drains, so the
unwritten backlog becomes a second copy of memory the engine believed it had
freed.

**Observed?** Yes: "0.5 → 10.3 GB in ten seconds".

**Property.** Safety invariant on backlog bytes, and the accounting identity
`accounted = live + backlog + pool`, which is what makes the backlog visible to
admission at all.

### F6 — Stranded value

**Mechanism.** A value is dropped from RAM that is neither durable nor
recomputable, and a consumer later needs it. `_recomputable` guards this by
refusing to drop loop and sequence nodes, which the executor cannot re-run.

**Observed?** The guard exists, which suggests the hazard was found rather than
anticipated.

**Property.** Safety: for every value with a pending consumer, it is resident, or
durable, or recomputable. Structural, and a good fit for Alloy: the counterexample
is a small configuration of sets and it reads directly.

### F7 — Double computation

**Mechanism.** Two workers compute the same node. Content addressing forbids it;
`DoubleComputationError` (`node_table.py:61`) is the guard. The fusion planner is
one place where it nearly happened: a cone member claimed by the planner must not
also be enqueued.

**Property.** Safety: at most one in-flight computation per node id. Cheap to
property-test once the harness exists, and a natural invariant.

### F8 — Use-after-evict race

**Mechanism.** The event loop rematerializes a dependency, dispatches, and then
another worker's reclaim sweep evicts that same value before the pool thread's
`table.values[dep_id]` lookup happens. Result: `KeyError` mid-kernel.

**Observed?** Yes — `_dispatch_pins` exists to close exactly this window, and its
comment describes the race in detail, including that "rematerialize (event loop)
happens-before dispatch, but the pool thread's actual lookup can land at any point
during the `await`, on a different OS thread, with no lock between it and this
coroutine's own eviction sweep."

**Why testing is unreliable.** It is a genuine interleaving bug. It reproduces
under load and not under test, and its absence from a test run is not evidence.

**Property.** Safety under all interleavings: a pinned value is never evicted.
This is the strongest argument in this document for model checking rather than
testing, because the state space of the pin protocol is small and the property is
exactly what enumeration is good at.

### F9 — Recompute thrash

**Mechanism.** Eviction frees a value that is immediately needed, so it is
recomputed, which allocates, which triggers eviction. The system makes progress in
the sense that operations retire, but throughput collapses and the work is
self-inflicted.

**Observed?** Yes: 246,318 recomputes in one run, "the highest of any run, and a
3% net slowdown even though peak memory was bounded correctly" — which is what
motivated capping the ownerless share.

**Why it matters here.** Thrash is *not* a liveness violation: the run terminates.
It is an efficiency property, and it is the failure mode most likely to be
mistaken for the livelock of F2. Distinguishing them requires counting recomputes,
which the engine does.

**Property.** A bound on recomputes per node, or on total recompute work as a
fraction of useful work. Best expressed as a property test with a threshold rather
than a formal property.

### F10 — Silent write loss

**Mechanism.** `sqlite3.OperationalError: database is locked` raised by a batch
write is caught by a handler intended for *unserialisable values*, which retries
the batch one entry at a time. Decomposition cannot help a lock: the database is
equally locked for each of the N retries, so the batch is dropped.

**Observed?** Yes, issue #39: 243 values lost in a single 13-minute run when a
second process held the write lock.

**Property.** Durability: a value the engine reports as persisted is on disk.
Reachable by fault injection, which is cheaper and more convincing than a model
here.

### F11 — Silent policy no-op

**Mechanism.** A policy change that has no effect, and nothing says so. Two
instances in this repository's recent history: an `--sparse-cache` filter placed at
write time, where the values it was meant to skip are still live (it skipped
almost nothing, and the measurement — 34.2 → 44.7 values/s — was what revealed
it); and `getattr(obj, "feature", default)` probing between modules that ship
together, where a rename degrades to "feature silently off".

**Property.** Refinement: the code does what the specification of the policy says.
This is where contracts and property tests belong, not a model checker — and where
**observability is the primary defence**: a policy whose effect is reported (the
count of values skipped, the bytes not written) is a policy whose no-op is visible.

### F12 — Progress-reporting incoherence

**Mechanism.** Two counters, no defined relationship: `len(table.completed)` read
by the watchdog, and the memlog's node column rendered on the progress bar. During
the incident they behaved differently, and the disagreement made every subsequent
measurement unfalsifiable.

**Property.** Not a property of the engine but of its instruments: all observers
of "progress" report the same quantity. Fixed by API discipline (issue #41), not
by verification. Listed here because it *disabled* verification of everything
else.

### F13 — Watchdog blindness

**Mechanism.** The stall watchdog's raise path requires nothing in flight; with any
kernel executing, the only reachable branch prints every 3600 s and resets its
idle timer. Deliberate — an nnU-Net training is a single node that legitimately
runs for two hours — but it cannot distinguish one slow kernel from fifteen wedged
ones, and no check observes the completion rate.

**Property.** A real-time property: *if a kernel exceeds its expected duration by
a factor k, a report is emitted within Δ*. Real time is where TLA+ is awkward and
**UPPAAL** is natural, and the model is small enough to be self-contained.

### F14 — Crash

**Mechanism.** Two kinds. OOM kill (F4's consequence). And genuine crashes inside
native code: `persist.py` carries a diagnostic trace for "the SIGSEGV inside
gzip", and the project's own notes record exit 245 (SIGSEGV) from a multithreaded
race inside a SimpleITK filter.

**Scope.** Largely outside formal methods: a segfault in a third-party C++ library
is not a property of this engine's protocol. What *is* in scope is that a crash
must not lose committed work and must be diagnosable — hence `faulthandler`,
lineage-based recovery, and torture testing.

### F15 — Data race under free threading

**Mechanism.** The engine runs with `PYTHON_GIL=0`. Several shared structures are
accessed without locks, justified in comments by atomicity: "these are per-key
dict puts under a content hash (idempotent) and CPython dict operations are
GIL-atomic, so no lock is needed" (`engine/expander.py:25`); "single set ops from
this thread are GIL-atomic; no lock needed" (`engine/persist.py:144`); "single
membership tests are GIL-atomic and staleness only shifts an eviction preference"
(`storage.py:644`).

**Status: unaudited, and the justification's *wording* is obsolete.** Under
free-threading there is no GIL to make anything atomic; individual operations on a
`dict` or `set` remain atomic because each object has its own lock, so most of
these conclusions probably still hold. But the *reason* given no longer applies,
and the reasoning pattern does not extend to **compound** operations —
check-then-act, read-modify-write, or any invariant spanning two containers. The
engine has several such patterns by construction: `_reclaim_memory` reads
`table.values`, `graph.consumers`, `_dispatch_pins` and `table.persisted(nid)` for
one decision, on the event loop, while workers mutate all four.

**Recommended action:** an explicit audit that re-derives each claim in
free-threading terms and records the argument, rather than a formal method. Where
an invariant spans containers, the honest options are a lock, a single atomic
structure, or an explicit statement that a stale read is harmless *and why*. Some
comments already do the last of these well; the point is to make it uniform.

---

## 5. Property taxonomy

Terminology, because the technique follows the property class.

**Safety** — something bad never happens. Expressible as an invariant over states:
`accounted ≤ hard`; at most one in-flight computation per node; the buckets
partition the live tier. Checkable by executable assertions, by property tests, and
exhaustively by a model checker on a small configuration. Provable for *all*
configurations if an **inductive invariant** can be found (preserved by every
transition), which is what Apalache does for TLA+ and what makes the difference
between "no bug in models up to size 4" and "no bug".

**Liveness** — something good eventually happens. Requires **fairness**
assumptions, without which any scheduler can be starved by an adversary: *weak*
fairness (an action continuously enabled is eventually taken) suffices for the
workers and the writer; the queue discipline may need *strong* fairness (an action
repeatedly enabled is eventually taken) unless an explicit anti-starvation rule is
added. F2 and F3 are liveness.

**Real-time** — bounded response. "A kernel overdue by factor k is reported within
Δ." Needs a timed formalism; discretising time into a temporal logic is possible
but clumsy and error-prone. F13.

**Resource** — a quantitative bound. `accounted ≤ hard` is safety with arithmetic
in it, which matters practically: the arithmetic is what makes a model's state
space explode, and abstracting bytes into a handful of units is the standard cure
and the standard source of unsoundness (§8).

**Refinement** — the implementation only does what the specification allows,
formally trace inclusion (every behaviour of the code is a behaviour of the
model). This is the property that ties a model to code, and §7 is about how to get
it without proving it.

---

## 6. Techniques

For each: what it establishes, what it costs, and whether it fits here.

### 6.1 Executable invariants (assertions over a state snapshot)

Not formal verification; the foundation for it. A frozen `Snapshot` of engine
state and a registry of pure predicates over it, each returning a violation with
witnesses. Run strictly in a torture configuration, sampled and logged in
production, and called directly by tests.

**Establishes:** safety, on the executions that actually happen.
**Cost:** ~1 week. **Fit:** excellent, and a prerequisite for everything else,
because it forces the invariants to be written down in a form a machine reads.
Tracked as issue #45.

### 6.2 Property-based and model-based testing

`hypothesis` over a deterministic harness: a fake executor where each node
declares `(cost_ms, output_bytes)`, a fake storage with declared write bandwidth
and injectable failures, and a virtual clock so a 100k-node run takes
milliseconds. `hypothesis.stateful` for schedules. Then: termination for any DAG
and any budget; `accounted ≤ hard` throughout; results equal to an unbounded-budget
reference; no invariant violated; no double computation.

**Establishes:** safety and *bounded-horizon* liveness on many generated
executions. Not a proof, but the highest ratio of confidence to effort available.
**Cost:** ~1 week including threading the clock. **Fit:** excellent. Tracked as
issue #46, with the acceptance criterion that it reproduce the most recent
regression in under a second.

### 6.3 Model checking a resource-protocol abstraction

**TLA+ / PlusCal, with TLC and Apalache.** Temporal properties and fairness are
first class, so the liveness list is expressible directly. TLC enumerates small
configurations exhaustively — 3 nodes, 2 workers, budget 3 units — which is where
protocol bugs live. Apalache checks *inductive invariants* symbolically, lifting
safety results beyond a bounded model size. PlusCal keeps the specification close
to the algorithm and reviewable by someone who does not read TLA+ fluently.
*Weakness:* real time models badly (F13 needs another tool).

**mCRL2.** Process algebra with modal μ-calculus properties. **Divergence —
livelock — is a first-class analysis** rather than an encoding, which is a real
advantage given that F2 is the central open question. *Weakness:* quantitative
state must be abstracted hard; smaller toolchain.

**SPIN / Promela.** Strong at deadlock detection between communicating processes.
*Weakness:* integer budgets explode the state space, and the channel idiom fits an
actor system better than an event loop over shared collections. Reasonable for the
F8 pin protocol in isolation.

**Alloy 6.** Relational, with temporal operators since v6. Best-in-class
counterexample readability, and the structural invariants (F6, bucket partition,
queue membership) are pure structure. *Weakness:* fairness and long traces.
**Recommended as a cheap addition regardless of the main choice.**

**UPPAAL.** Timed automata; the right and probably only comfortable tool for F13.
Small self-contained model, not a substitute for the protocol model.

**Cost:** weeks each, plus ongoing maintenance as the protocol changes.
**Fit:** this is the only family that addresses F1, F2, F3 and F8 as *proofs*
rather than as sampling.

### 6.4 Deductive verification and symbolic execution of the code

**CrossHair** — symbolic execution of Python functions against contracts.
Verifies *the code*, not a model of it, which is exactly the gap §7 worries about.
Applicable to pure, typed, small functions: precisely the invariant predicates of
§6.1 and any pure transition functions extracted per §7. **Cheap; recommended.**

**Nagini (Viper)** — deductive verification of Python with contracts, including
some concurrency. In principle the strongest code-level option; in practice its
supported subset excludes `asyncio` and much of the standard library, so it would
apply only to carved-out pure modules, where CrossHair already suffices at a
fraction of the effort.

**Deliberately excluded.** Coq / Isabelle / Lean: a mechanised proof is a
multi-month project and the protocol is still changing. Dafny: a verified
reference implementation would have to be hand-ported to Python, so the proof
would not cover the code that runs. CBMC, Infer's starvation analysis: wrong
languages.

### 6.5 Runtime verification

Synthesise **monitors** from the specification and run them against the real
system: a violated invariant, or a trace outside the model's allowed language, is
reported at runtime. This is the pragmatic middle between testing and proof, and
it is what §6.1 becomes once the predicates are derived from a specification
rather than written by hand.

**Fit:** very good here, because the engine's failures are rare and
load-dependent — exactly the population that testing under-samples and production
over-samples.

### 6.7 A note on external profilers: they do not work here

Worth recording because it changes the cost of everything else. `py-spy` cannot
read this interpreter. Tested with py-spy as the **parent** process, so
`ptrace_scope = 1` is satisfied and no privileges are needed: against a healthy
31-case batch it produced a 921 KB profile containing **one** frame, a synthetic
process placeholder, and zero Python frames across all 33 thread profiles. This
is free-threaded CPython 3.14 and the profiler does not support it.

**Consequence.** There is no external route to a stack trace. Every diagnostic
must be **in-process**: `faulthandler`, a signal-triggered dump, or invariants
evaluated by the engine itself. That is an argument for §6.1 and §6.5 over any
plan that assumes a profiler can be attached when something goes wrong, and it
raises the priority of the on-demand dump (#42) from convenience to necessity.

A zero-code partial mitigation is in place: `run_iter.sh` exports
`PYTHONFAULTHANDLER=1`, so `kill -ABRT <pid>` prints every thread's stack before
aborting. Destructive, but a run that has stopped advancing was going to be
killed anyway.

### 6.6 Abstract interpretation

Sound static analysis by over-approximating program semantics in an abstract
domain (intervals, octagons, ...). Excellent for numeric bounds in a static
program; poor fit here, because the quantities that matter (`accounted`, backlog
bytes) depend on scheduling, data sizes and an asynchronous writer, so any sound
abstraction would be too coarse to say anything useful. Mentioned because it is a
natural thing to reach for and, in this instance, would waste the effort.

---

## 7. Binding models to code

This is the question that decides whether §6.3 is an investment or a hobby. A
model that is not tied to the code proves something about a fiction. Six options,
with a recommendation.

### 7.1 Option A — specification as documentation, reviewed by humans (or an agent)

Write the model, check it, keep it in `doc/formal/`, and rely on review to keep
code and model aligned.

**Cost:** lowest. **Value:** real but easily overstated. The model becomes
*documentation with a consistency check* — better than prose, because
contradictions are caught — but the binding is entirely social. When the code
changes under deadline, the model does not, and six months later nobody knows
which is right.

**Verdict:** necessary as a baseline, insufficient alone. And a specific caution
applies when the reviewing agent is an LLM: my own record on this codebase in a
single day includes three confidently-stated mechanisms that were wrong, one of
which I published as an issue before reading the relevant function. A green model
check *reduces* the visible signals of that failure mode, because it looks like
proof. If an agent authors the model, the **abstraction assumptions must be
reviewed by a human separately from the specification** — they are a page, they
are where the error hides, and they are cheap to read.

### 7.2 Option B — code generation from the model

Generate the implementation (or its core) from the verified specification.

**Cost:** high. **Problems here specifically:** the engine's performance depends
on details a model deliberately abstracts (buffer pooling, batch sizes, when to
touch a global lock); generated code in Python would fight the free-threaded
tuning that took measurements to get right; and the generator becomes a dependency
nobody in the project maintains. **Verdict: no.** The protocol is not the
performance-critical part, and the parts that are cannot be generated.

### 7.3 Option C — model extraction from the code

Derive the model automatically from the source, so it cannot drift.

**Cost:** high, and fragile. Extraction from dynamic Python with `asyncio`, thread
pools and duck-typed collaborators would either need heavy annotation or produce a
model too coarse to check. **Verdict: no**, though a *partial* extraction —
generating the state-machine skeleton from a declarative transition table (§7.6) —
is exactly the recommendation, viewed from the other side.

### 7.4 Option D — conformance testing (trace inclusion, checked by test)

Define the model's actions, mirror them as `hypothesis.stateful` rules against the
real engine, and assert that every observed trace is one the model allows. This is
**refinement checked by sampling**: not a proof of trace inclusion, but a
mechanical, continuously-run check that the code has not left the model's
language.

**Cost:** moderate, and mostly shared with §6.2, which is being built anyway.
**Value:** high — it is the only option in this list that makes drift *fail a
test*. **Verdict: yes, essential.** Without it, §6.3 is Option A with extra steps.

### 7.5 Option E — runtime monitors derived from the specification

Compile the specification's invariants into predicates the running engine
evaluates (§6.1, §6.5). The binding is that the *same* invariant text is the model
checker's input and the monitor's source.

**Cost:** low once invariants exist. **Value:** catches the case tests miss —
production interleavings at production scale. **Verdict: yes.**

### 7.6 Option F — restructure so the protocol *is* a pure core (recommended)

The binding problem is hard because the protocol is currently spread across
mutable state in `core.py`, `node_table.py`, `governor.py`, `admission.py` and
`persist.py`, entangled with I/O and threads. Most of it need not be.

**The proposal.** Extract the decision logic into a pure, deterministic module —
call it `engine/protocol.py` — with:

- an immutable `ProtocolState` (sets of node ids per lifecycle state, integer byte
  counters, queue orders, budget and ceiling);
- an `Event` type (`Completed(nid, bytes, cost_ms)`, `WriteLanded(nid)`,
  `SweepTick`, `Sampled(rss)`, `Admitted(nid)`, ...);
- pure transitions `step(state, event) -> (state, list[Action])`, where `Action`
  is `Evict(nid)`, `Spill(nid)`, `Drop(nid)`, `Admit(nid)`, `Park`, `Report(...)`;
- **no I/O, no threads, no clock** — the clock is an input.

Then the imperative engine becomes an *adapter*: it observes reality, produces
events, calls `step`, and performs the returned actions. Reclaim's three passes,
admission's rules, the sacrifice ramp and the spill decision all move inside; what
stays outside is genuinely about the world.

**Why this solves the binding problem.** Because `step` is pure and deterministic:

- **the model checker's specification can be a transcription of `step`**, small
  enough that a reviewer can compare them side by side — and a transcription of a
  hundred-line pure function is a far more honest artifact than a model of a
  distributed system's prose description;
- **CrossHair can symbolically execute `step` directly**, verifying the code
  rather than the transcription, which closes the drift gap for the safety
  properties it can reach;
- **conformance tests (§7.4) become trivial**, because the model's actions *are*
  the implementation's events;
- **the harness of §6.2 gets smaller**, since most property tests need only `step`
  and not the whole engine;
- and, independent of verification, the code gets better: policy decisions become
  reviewable in one place, testable without an event loop, and impossible to make
  accidentally dependent on I/O ordering.

**Cost.** Substantial — this is a refactor of the most delicate code in the
project, and it must be behaviour-preserving. It should be done *after* §6.1 and
§6.2 exist, because those are what make "behaviour-preserving" checkable.

**Risk.** A refactor that changes performance. Mitigation: the decision logic is
not the hot path — kernels are — and the adapter keeps the hot path where it is.
That claim should be measured, not assumed, before the refactor is accepted.

### 7.7 Recommended combination

| Layer | Technique | Binding |
|---|---|---|
| 1 | Executable invariants (#45) | direct: code asserts them |
| 2 | Deterministic harness + property tests (#46) | direct: exercises the real scheduler |
| 3 | Pure protocol core (§7.6) | direct: the protocol becomes a reviewable artifact |
| 4 | CrossHair over the core | direct: verifies the code |
| 5 | Alloy for structural invariants | abstraction, reviewed |
| 6 | TLA+/PlusCal (or mCRL2) over the core | transcription, reviewed side by side |
| 7 | UPPAAL for the watchdog | abstraction, self-contained |
| 8 | Conformance tests model↔code (§7.4) | **mechanical: drift fails a test** |
| 9 | Runtime monitors in production (§7.5) | **mechanical: violations are logged** |

Layers 8 and 9 are what make 5–7 worth doing. Anything that leaves them out is
Option A wearing a formal hat.

---

## 8. Pitfalls

Specific to this project, in rough order of likelihood.

**P1 — Verifying a model of a system nobody has.** The commonest failure of formal
methods in practice. The model omits the writer's latency, or discretises bytes so
coarsely that the interesting state is unreachable, and the check passes because
the bug cannot be expressed. *Mitigation:* Layers 8 and 9 above; a written list of
abstractions in every model's README; and one deliberate exercise — reintroduce a
known historical bug into the model and confirm the check fails. A model that
cannot catch a bug we already had is not validated.

**P2 — Mistaking bounded checking for proof.** TLC on 3 nodes and 2 workers proves
nothing about 369 cases and 24 workers. It finds *protocol* bugs, which are usually
small-configuration bugs, but the distinction must stay explicit in every claim.
*Mitigation:* use Apalache for inductive invariants where an unbounded safety
result is wanted, and say "checked up to N" everywhere else.

**P3 — State explosion, then unsound abstraction to escape it.** Bytes and
`compute_ms` are the explosive dimensions. The cure — one byte unit per value, a
two-valued cost — is also the most likely source of P1. *Mitigation:* make
abstraction a reviewed artifact, and check whether each abstracted dimension can
be shown irrelevant by a monotonicity argument rather than by hope.

**P4 — The agent's failure mode: confident, wrong, and now formalised.** Covered in
§7.1. The specific danger is that a specification authored by an LLM reads as
authoritative precisely where it is most likely to be wrong (the abstraction
choices), and the model checker will not object. *Mitigation:* human review of
assumptions separately from the specification; conformance testing as the arbiter;
and a standing rule that **when the model and the implementation disagree, the
implementation is the evidence and the model is the hypothesis** until the
disagreement is explained.

**P5 — Fairness assumptions doing the work.** A liveness proof under strong
fairness on every action can be vacuous: it may be assuming away the starvation it
claims to exclude. *Mitigation:* state fairness assumptions explicitly, prefer weak
fairness, and treat any property that needs strong fairness as a finding — it means
the implementation needs an explicit rule, not that the property is proven.

**P6 — Maintenance decay.** A specification that is not run in CI is dead within
two months. *Mitigation:* CI runs TLC on the small configuration, CrossHair on the
core, and the conformance tests; a model whose check is not automated should not be
merged.

**P7 — Verifying the wrong layer.** Segfaults in SimpleITK, `database is locked`,
and OOM kills are not properties of this protocol. Formal methods applied to them
would be wasted; fault injection and torture testing are the right instruments.
*Mitigation:* the table in §2 assigns a technique per class deliberately; resist
the pull to model everything.

**P8 — Observability regressions invalidating everything downstream.** The
incident's deepest lesson: two disagreeing progress counters made every subsequent
measurement unfalsifiable, including the ones used to judge a hypothesis. Any
verification programme rests on instruments; the instruments need their own
consistency checks. *Mitigation:* issue #41 first, and an invariant asserting that
all progress observers agree.

**P9 — Refactoring for verifiability and losing performance.** §7.6 moves the
decision logic; if it accidentally moves the hot path, the project pays for
verification in throughput. *Mitigation:* measure before and after with the
existing bandwidth and concurrency probes, on a real sweep, and treat a regression
as a blocking objection.

**P10 — Proving properties nobody needed.** It is easy to verify the elegant part
and leave the ugly part unverified. The properties that matter here are the three
in §1; anything else is optional. *Mitigation:* every model's README states which
of F1–F15 it addresses, and a model addressing none of them is not merged.

---

## 9. A staged plan

Each stage is useful alone and makes the next cheaper. Issue numbers refer to
`VoxLogicA-Project/VoxLogicA-2`.

| Stage | Content | Effort | Addresses |
|---|---|---|---|
| 0 | One progress counter (#41); SIGUSR1 state dump (#42) | days | P8, F12, and makes F2 diagnosable |
| 1 | Live dashboard: rate, memory buckets, in-flight ages (#44) | days | F2 vs F9 discrimination |
| 2 | Rate watchdog + per-kernel deadlines (#43) | 2–3 days | F13 |
| 3 | Executable invariants (#45) | ~1 week | F4, F5, F6, F7 |
| 4 | Deterministic harness + property tests (#46); torture mode (#47) | ~1.5 weeks | F4, F7, F9, F10, F14 |
| 5 | Free-threading audit of the lock-free claims | ~3 days | F15 |
| 6 | Pure protocol core (§7.6) | weeks | prerequisite for 7 and 8 |
| 7 | CrossHair over the core; Alloy for structural invariants | ~1 week | F6, plus safety on the core |
| 8 | TLA+/PlusCal or mCRL2 over the core, with conformance tests (#48) | weeks | **F1, F2, F3, F8** |
| 9 | UPPAAL for the watchdog | days | F13, as a proof rather than a heuristic |

Stages 0–2 are the ones to do before arguing about the rest: they cost days, and
they are what turn the next incident from an archaeology exercise into a reading.
Stage 8 is the only stage that answers the question in §1.1.

---

## 10. Glossary

**Safety property** — an invariant: something bad never happens. Violated by a
finite execution, so testing can in principle find a violation.

**Liveness property** — something good eventually happens. Violated only by an
infinite execution, so testing can never *establish* a violation, only observe a
suspicious timeout.

**Fairness** — an assumption restricting the schedules considered. *Weak fairness*:
an action continuously enabled is eventually taken. *Strong fairness*: an action
repeatedly enabled (possibly with gaps) is eventually taken. Liveness proofs are
meaningless without stating which is assumed.

**Deadlock** — no action is enabled; the system is stuck and idle.

**Livelock** (in process algebra, **divergence**) — actions are enabled and taken
forever, but no progress is made; the system is stuck and busy.

**Starvation** — global progress continues but some component never advances.

**Inductive invariant** — a predicate true initially and preserved by every
transition. Establishes a safety property for all reachable states, of any size,
rather than for a bounded model.

**Bounded model checking** — exhaustive exploration up to a fixed size or depth.
Finds bugs; does not prove absence beyond the bound.

**State explosion** — the state count growing combinatorially with parameters.
The reason models abstract away quantities like bytes.

**Abstraction** — a simplified model over-approximating the system.
**Sound** if every real behaviour is a model behaviour (so a proof about the model
transfers); **complete** if the converse holds. An unsound abstraction proves
nothing; an incomplete one produces **spurious counterexamples**: model
behaviours the real system cannot exhibit.

**Refinement / trace inclusion** — every behaviour of the implementation is a
behaviour of the specification. The formal statement of "the code matches the
model".

**Conformance testing** — checking refinement by sampling: run the implementation,
check each observed trace against the specification.

**Runtime verification** — evaluating specification-derived monitors on the live
system, so violations are detected in production rather than in a test.

**Content addressing** — identifying a value by a hash of the expression that
produces it. Here it is why values are regenerable, and therefore why the engine's
risks are about progress and resources rather than about wrong answers.

---

## 11. How to review this document

The parts most worth attacking, in order:

1. **§7.6** — is extracting a pure protocol core the right investment, or is it a
   rewrite of the most delicate code in the project justified by a verification
   benefit that may not materialise? This is the largest commitment proposed here.
2. **§2, rows F2 and F8** — is model checking really the answer for those two, or
   would the deterministic harness plus a free-threading audit get there for a
   tenth of the cost?
3. **§6.3, TLA+ versus mCRL2** — deliberately unsettled. Divergence being a
   first-class check in mCRL2 is a real argument for it given that F2 is the open
   question.
4. **§4, F15** — the free-threading audit. This document flags it and does not
   resolve it; if the lock-free claims are in fact fine under PEP 703, that should
   be written down once, properly, rather than remaining an inherited assumption.
5. **§8, P4** — whether an LLM-authored specification is acceptable at all, and if
   so under what review discipline.
