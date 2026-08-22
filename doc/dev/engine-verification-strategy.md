# Verifying the engine: why a five-hour stall was undiagnosable, and what to change

Written 2026-08-22, after a 369-case sweep stopped advancing and three
successive explanations for it turned out to be wrong. The point of this
document is not the incident. It is that the incident **could not be settled
with the instruments the engine provides**, and that this is fixable.

## 0. What actually happened, and what is still unknown

A single-process run of `brats023_descent_369.imgql` ran at 250–350 node/s for
five hours, then advanced no further. Measured at that point:

| observation | value |
|---|---|
| event-loop thread CPU | 64.6% |
| each of 24 worker threads | ~1.1% |
| memlog node counter, over 30 s | unchanged |
| `accounted` / `budget` | 24.5 GB / 24.7 GB |
| `undurable` / `durable` | 20.0 GB / 2.3 GB |
| `running` / `ready` | 15 / 41 |
| largest live operator | `vox1.mask`, 21.2 GB |
| watchdog lines printed | **0** |

The last row contradicts the third. `_join_with_watchdog` prints a line after
`VOXLOGICA_HANG_TIMEOUT_S` (3600 s) without a completion; five hours of true
zero-progress would have printed four or five. So either completions were still
happening and the memlog counter tracks something else, or the watchdog's notion
of progress and the memlog's disagree.

**I could not resolve that.** Not for lack of trying: `py-spy` needs elevated
permissions on the host, so no stack sample was obtainable, and the engine
offers no way to ask a running process what it is doing. Three hypotheses were
advanced and two are now known to be wrong (see §1.1). The third is unverified.

That is the finding worth acting on: *the engine cannot currently distinguish
"deadlocked" from "100× slower", from the outside or from the inside.*

## 1. What the code actually does

### 1.1 The resource protocol is more complete than the incident suggested

`_reclaim_memory` (core.py) is not a single eviction path. It is three passes
with four distinct exits, and reading them closely retracts two claims I made
while diagnosing this:

- **PASS 0** frees *ownerless* values — zero remaining consumers, so no write and
  no future read. Runs when over budget **or** when speculation exceeds its
  share (`_ownerless_share = 0.25`).
- **PASS 1** evicts values whose write has landed.
- **PASS 2**, per candidate: evict if durable; else **drop and recompute** if
  `compute_ms < sacrifice_ms` and `_recomputable(nid)`; else **force a write**
  via `table.spill(nid)`; else requeue.

So "the engine should force-write under pressure" and "the engine should
drop-and-recompute" — the two fixes I proposed in issue #40 — **are already
implemented**. That issue is wrong on those points and needs correcting.

`sacrifice_ms` even ramps with RSS pressure (`governor.sacrifice_ms`), on the
stated reasoning that near the ceiling the alternative to sacrificing a 200 ms
recompute is being killed and losing everything.

### 1.2 The watchdog cannot fire while anything is executing

```python
deadlocked = (self._in_flight == 0 and self.ready.qsize() == 0
              and self.admission.active_jobs == 0)
if idle >= hard and self._in_flight > 0:
    print(f"[watchdog] {idle:.0f}s without a completion, but "
          f"{self._in_flight} kernel(s) still executing — waiting. ...")
    idle = 0.0
    continue
```

With `_in_flight = 15`, `deadlocked` is false by construction and the only
reachable branch prints and resets. The escape hatch is deliberate and its
reason is sound — an nnU-Net training is one node that legitimately takes two
hours, and an earlier version killed such a run. But the guard conflates two
very different states:

- one kernel that is *supposed* to take hours, and
- fifteen kernels that are not finishing.

Nothing distinguishes them, **even though the data to distinguish them already
exists**: `compute_ms` is recorded per node in the store, so a per-kernel
expectation is derivable from history rather than from a global constant.

### 1.3 There is no throughput floor

Every check is binary: zero completions, or not zero. A collapse from 340 to
3 node/s is invisible to all of them. On a run whose total is data-dependent —
loops unroll as they go, so the progress bar's denominator grows with its
numerator — a rate collapse is also invisible to a human reading the bar. The
ETA read "2 hours" for five hours.

### 1.4 The invariants are stated precisely, in prose, and checked nowhere

The comments in this engine are unusually good. They state real invariants:

- "Buckets are disjoint, first match wins" (`_memory_snapshot`)
- "every value is regenerable from lineage" (`liveness.py`)
- "A queued write is not reclaimed memory; it is the same memory, twice"
  (`node_table.spill`)
- "admission only gates NEW work: it cannot reclaim memory already committed"

None is executable. Nothing asserts that the buckets partition the live tier,
that `accounted == live + backlog + pool`, or that every resident value sits in
exactly one reclaim queue. The memlog's `untracked_n` field is a hand-rolled
instance of exactly this idea — it counts values on neither reclaim queue — and
it is *observed*, never *asserted*. It read 26 during the incident and nothing
happened.

### 1.5 Collaborators are discovered by `getattr`

`hasattr(backend, "set_live_probe")`, `getattr(persister, "skipped_dead", 0)`,
`getattr(self.table, "_persister", None)`. Optional-attribute probing means an
interface can drift silently: a renamed method degrades to "feature off" instead
of failing. There is no `pyproject.toml` and no type-checker configuration in
the repository — only `pytest.ini`.

### 1.6 Scale-dependent behaviour cannot reach the test suite

`tests/unit/test_memory_backpressure.py` is 1136 lines and drives real programs
through `ExecutionEngine`. But the kernels are scalar arithmetic: the failure
modes that matter here need values with *declared size and cost* — a 4 MB output
from a 135 ms kernel — at thousands of nodes, against a budget small enough to
bite, with a writer slow enough to saturate.

The proof that this gap is real: a `--sparse-cache` change of mine passed 1022
unit tests and a 30-case run, and wedged the 369-case run. The bug was a
one-line policy decision whose consequence only appears when the live tier
approaches the budget.

## 2. The strategy

Ordered so that each layer is useful on its own, and each makes the next cheaper.

### Layer 0 — one definition of progress

Today `len(self.table.completed)` (watchdog) and the memlog's node column can
disagree, and during the incident they did. Pick one counter, define it
("kernel invocations retired"), expose it in one place, and have every consumer
— watchdog, progress bar, memlog, tests — read that. An observability system
whose two clocks disagree cannot be used to settle an argument.

*Effort: small. Value: this alone would have told me within minutes which of my
three hypotheses was right.*

### Layer 1 — invariants as executable predicates

A new `engine/invariants.py`: pure functions over an immutable snapshot of
engine state, each returning a violation or `None`. Start with the ones already
written as comments:

```python
def buckets_partition_live_tier(s: Snapshot) -> Violation | None: ...
def accounted_equals_live_plus_backlog_plus_pool(s: Snapshot) -> Violation | None: ...
def every_resident_value_is_in_exactly_one_reclaim_queue(s) -> Violation | None: ...
def progress_is_possible(s) -> Violation | None:
    # ready > 0, or a kernel is in flight, or nothing is outstanding
def reclaim_has_an_exit(s) -> Violation | None:
    # over budget => some candidate is durable, or sacrificeable, or spillable
```

The last one is the incident, stated as a predicate. Three uses, one definition:
asserted in torture mode, sampled every N seconds in production (logged, not
raised), and called by tests.

*Effort: medium. Value: turns the good comments into things that fail loudly.*

### Layer 2 — a deterministic scheduler harness

The scheduler's decisions depend on the graph, the sizes, the budget and the
completion order — not on SimpleITK. Extract three seams that already almost
exist (`Executor`, `StorageBackend`, and the clock) and provide fakes:

- **fake executor**: each node declares `(cost_ms, output_bytes)`; "computing" is
  advancing a virtual clock;
- **fake storage**: declared write bandwidth, so the writer can be made to
  saturate on demand;
- **virtual clock**: the whole run executes in milliseconds of real time.

Then property-based tests over random DAGs and random budgets:

- the run always terminates;
- `accounted` never exceeds the hard ceiling;
- every goal's value is correct (the fake kernels are pure functions of their
  inputs, so results are checkable);
- no invariant from Layer 1 is ever violated.

*Effort: the largest item here, perhaps a few days. Value: the only way to get
real confidence about termination. Every scale-dependent bug in this document
would be reachable in a unit test.*

### Layer 3 — torture mode in CI

`VOXLOGICA_TORTURE=1`: budget shrunk to a few hundred MB, writer throttled,
`spill()` made to fail at random, eviction made maximally aggressive, invariants
asserted rather than logged. Then run the *existing* suite under it. Cheap to
build once Layer 1 and 2 exist, and it turns "only reproduces at 369 cases" into
"reproduces in the test suite".

### Layer 4 — a rate watchdog, and per-kernel deadlines

Two changes to `_join_with_watchdog`:

- **Rate, not zero.** Track completions per interval; warn when the rate falls
  below a fraction of its own trailing median for N consecutive intervals. A
  100× collapse should be as loud as a full stop.
- **Per-kernel deadlines instead of a global in-flight exemption.** Record the
  start time of each in-flight node; a kernel is late relative to *its own*
  expectation, derived from `compute_ms` history in the store (already
  recorded), with a generous multiplier and a floor for first-ever runs. Then
  the nnU-Net node gets its two hours and fifteen wedged masks do not.

*Effort: small-to-medium. Value: this incident becomes a message instead of a
mystery.*

### Layer 5 — forensics that do not need root

- `faulthandler.enable()` always, so a hard crash prints stacks.
- A `SIGUSR1` handler that dumps every thread's stack plus the Layer 1 invariant
  report to a file. `kill -USR1 <pid>` then replaces `sudo py-spy`, which is what
  blocked this investigation.
- `_dump_stuck()` already exists and is good; make it reachable on demand rather
  than only on the watchdog's raise path.

*Effort: small. Value: high, and it is the item I most wanted during the
incident.*

### Layer 6 — static checking

Add a `pyproject.toml` with mypy configured strictly for `voxlogica/engine/`
first (the rest can follow). Replace `hasattr`/`getattr` probing of internal
collaborators with `typing.Protocol` definitions: `SupportsLiveProbe`,
`SupportsBatchWrite`, and so on. Optional-attribute probing is appropriate at a
plugin boundary and misleading between modules that ship together.

### Layer 7 — a model of the value lifecycle

The protocol worth model-checking is small: a value is *computing*, *live*,
*queued-for-write*, *durable*, *evicted*, or *dropped*; memory is bounded;
consumers retire. A TLA+ or Alloy model of ~100 lines can be asked the exact
question this incident raised — *is there a reachable state where work is
outstanding, memory is at budget, and no reclaim exit applies?* — and answer it
for all schedules rather than for the ones we happened to run.

*Effort: medium, and it needs someone who enjoys it. Value: it is the only
technique on this list that says "never" rather than "not in these tests".*

## 3. Suggested order

1. Layer 0 (one progress counter) and Layer 5 (SIGUSR1 dump) — days, not weeks,
   and together they make the next incident diagnosable in minutes.
2. Layer 4 (rate watchdog, per-kernel deadlines) — makes it *reported* rather
   than merely diagnosable.
3. Layer 1 (executable invariants) — the foundation for everything below.
4. Layer 2 (deterministic harness) and Layer 3 (torture mode) — the real
   investment, and the one that would have caught the bug I shipped.
5. Layer 6 (types) alongside, opportunistically.
6. Layer 7 (formal model) when the protocol next changes shape.

## 4. What this document does not claim

It does not claim to know why the run stopped advancing. Sections 1.2 to 1.6
are properties of the code, read from the code, and each is independently
checkable. The incident is the motivation, not the evidence.
