# Making the engine verifiable: an incident that could not be diagnosed, and a plan

Status: proposal, for review.
Written 2026-08-22 against `2c4bbab`.
Author's note: I am the one who filed two wrong diagnoses of the incident below,
and one of the bugs that contributed to it. Both are documented here rather than
smoothed over, because the point of the document is that the engine let all three
mistakes go unnoticed for hours, and that is the thing worth fixing.

## 1. Purpose and audience

This document exists because a five-hour failure could not be diagnosed with the
instruments the engine provides, and three successive explanations for it were
wrong. It is written for anyone who may work on `voxlogica/engine/` — not only
for whoever hit this — so §2 reports the incident in enough detail to be
re-derived by someone who was not there, §3 states what the code does today with
file and line references, §4 names the defects, §5 proposes changes with concrete
designs and acceptance criteria, and §6 surveys formal-methods options rather
than asserting one.

Every claim in §3 is a property of the code, read from the code. §2 is an
observation log. §4 and §5 are argument, and are the parts to disagree with.

## 2. The incident

### 2.1 Environment

| | |
|---|---|
| host | fmt-5000, 61 GB RAM, 24 workers |
| interpreter | free-threaded CPython 3.14, `PYTHON_GIL=0` |
| engine commit | `d55a6dc` (the `--sparse-cache` headroom gate, since reverted) |
| program | `looping_experiment/brats023_descent_369.imgql`, all 369 BraTS2020 cases |
| command | `VOXLOGICA_VENV=ft ./run_iter.sh brats023_descent_369.imgql _scratch/b43c.out _scratch/sweep_v3.db` |
| store | `_scratch/sweep_v3.db`, warm, 5.7 GB of metadata and ~570 GB of payload |
| flags | none beyond the launcher's `--store-db` and `--cache-max-gb 0` |

The program is a coordinate-descent search: for each of 17 formulas it fixes one
parameter at a time, twice over, evaluating ~320 segmentations per case. Planned
work, measured by planning one case in isolation, is 25,308 nodes per case, so
about 9.3M nodes for the run.

### 2.2 Timeline

- **t=0 to t≈5 h.** Normal operation, 250–350 node/s, no errors logged, memory
  under budget. Node count reached ~3.7M, which is ~40% of the expected total.
- **t≈5 h onward.** No further advance. The tqdm line became byte-identical
  across samples 45 s apart, including its own elapsed clock. The run was left in
  this state and sampled for a further ~30 minutes before being killed.

The same program had wedged once before, at commit `016a47e`, under
`--sparse-cache`. That first occurrence was attributed to the flag, the flag was
gated on memory headroom (`d55a6dc`), and the run was restarted — and wedged
again with the flag off. So the flag is at most an accelerant.

### 2.3 What was measured at the wedge

From `top`, `ps -L` and the engine's own memlog (`/tmp/voxlogica-memlog-<pid>.tsv`):

| observation | value |
|---|---|
| event-loop thread CPU | 64.6% |
| each of 24 worker threads | ~1.1% |
| process total | 126% of 2400% available |
| memlog node counter, two samples 30 s apart | identical |
| `accounted` / `budget` / `hard` | 24.5 / 24.7 / 36.7 GB |
| bucket `undurable` | 20.0 GB |
| bucket `durable` | 2.3 GB |
| bucket `ownerless` | 2.1 GB |
| bucket `pinned` | 0.1 GB |
| bucket `write_queued` | ~0.0 GB |
| `untracked` / `untracked_n` | 0.1 GB / 26 |
| `running` / `ready` | 15 / 41 |
| largest live operator | `vox1.mask`, 21.2 GB |
| `[watchdog]` lines in the log | **0** |
| `ERROR` lines in the log | **0** |
| swap in / swap out (vmstat) | 0 / 0 |

### 2.4 The contradiction that was never resolved

The memlog node counter did not move across 30 s. But `_join_with_watchdog`
(core.py:451) prints a `[watchdog]` line after `VOXLOGICA_HANG_TIMEOUT_S`
(default 3600 s) without a completion, and **zero** such lines were printed in
five hours. Five hours of genuinely zero completions would have printed four or
five times.

So one of the following is true, and the available instruments cannot say which:

1. completions continued at some very low rate, and the memlog's node column
   tracks a different quantity than `len(self.table.completed)`; or
2. the two counters disagree for another reason and the watchdog's `idle` timer
   was being reset spuriously.

`write_queued ≈ 0` is a further complication for any memory-based explanation:
the writer's 512 MB backlog was **empty**, so `table.spill()` (node_table.py:423)
was not returning `False` for saturation, and PASS 2 of reclaim should have been
able to force writes freely.

### 2.5 What was attempted and did not work

- `py-spy dump`, `py-spy top`, `py-spy record`, all with `--nonblocking`:
  `Permission Denied — try running again with elevated permissions`. No stack
  sample was obtained. The engine offers no in-process alternative.
- `--engine-debug` was not enabled on the run, so `_dump_stuck()` (core.py:1484)
  was unreachable; it is called only from the watchdog's raise path and from
  `run()` when `self._debug`.
- Two hypotheses were published in issue #40 before `_reclaim_memory` had been
  read closely. Both were wrong; see §3.1. The third, that the event loop spins
  in reclaim, remains unverified.

### 2.6 The workaround in use

The same 369 cases split into twelve 31-case batches, one process per batch, run
sequentially. The first batch completed in 2536 s with `rc=0` and no stall. The
bounded working set never approaches the budget. Total wall time is comparable to
the single process's projection, so nothing is lost but the convenience of one
invocation.

## 3. What the code does today

### 3.1 The reclaim protocol is three passes with four exits

`ComputationEngine._reclaim_memory` (core.py:724), called on every worker turn
and bounded to `_EVICT_SWEEP = 256` candidates per pass (core.py:90):

- **PASS 0** — drop *ownerless* values: zero remaining consumers, so no write and
  no future read. Triggered when over budget **or** when ownerless bytes exceed
  `_ownerless_share = 0.25` of the budget (core.py:311). The second trigger
  exists because ownerless values are speculative cache, and measured once at
  23.1 GB of a 25 GB budget while evicting values with certain future reads.
- **PASS 1** — evict values whose write has landed. Kept on its own queue
  (`_spill_pending`) rather than shared with PASS 2, because sharing starved it:
  measured 110,495 spills against 1,972 evictions.
- **PASS 2** — per candidate, in order: evict if `table.persisted(nid)`; else
  **drop and recompute** if `compute_ms < sacrifice_ms` and `_recomputable(nid)`
  (core.py:885); else **force a write** via `table.spill(nid)`
  (node_table.py:423) and let PASS 1 collect it; else requeue.

`sacrifice_ms` is `max(config.persist_min_compute_ms, governor.sacrifice_ms)`,
and `governor.sacrifice_ms` (governor.py:180) ramps from 1 ms toward a maximum as
RSS pressure rises, on the stated reasoning that near the ceiling the alternative
to sacrificing a 200 ms recompute is being killed and losing every undurable byte
at once.

`table.spill()` deliberately ignores the worth-it gate but **not** the writer's
backlog budget (512 MB, `_persist_backlog_budget`, node_table.py:53), returning
`False` when saturated. Its docstring records why: an earlier version that
bypassed both grew the unwritten backlog from 0.5 to 10.3 GB in ten seconds while
the live copies it was meant to free stayed resident.

**Consequence for §2.5:** "the engine should force-write under pressure" and "the
engine should drop-and-recompute" are already implemented. Issue #40 asserted
they were missing; a correcting comment has been posted.

### 3.2 The watchdog cannot raise while anything is in flight

`_join_with_watchdog` (core.py:451):

```python
deadlocked = (self._in_flight == 0 and self.ready.qsize() == 0
              and self.admission.active_jobs == 0)
if idle >= hard and self._in_flight > 0:
    print(f"[watchdog] {idle:.0f}s without a completion, but "
          f"{self._in_flight} kernel(s) still executing — waiting. ...")
    idle = 0.0
    continue
if (idle >= stall and deadlocked) or (idle >= hard and self._in_flight == 0):
    ... raise RuntimeError(...)
```

With `_in_flight = 15`, `deadlocked` is false by construction, the first branch is
the only reachable one, and it prints and resets. The exemption is deliberate and
its reason is recorded in the docstring: an nnU-Net training is a single node that
legitimately takes two hours, and an earlier version's backstop killed such a run.

The guard therefore cannot distinguish:

- one kernel that is *supposed* to run for hours, from
- fifteen kernels that are not finishing.

The data to distinguish them already exists: `compute_ms` is recorded per node in
the results database (`results.compute_ms`), so a per-node expectation is
derivable from history.

### 3.3 Every liveness check is binary

`stall` (default 180 s) and `hard` (default 3600 s) both trigger on *zero*
completions. No check observes the completion *rate*. A collapse from 340 to
3 node/s satisfies every existing check.

This interacts badly with the progress display. The bar's denominator is the goal
count, and the node readout's denominator is discovered as loops unroll, so it
grows with the numerator: during the incident the gap between "done" and "known"
stayed at ~17,000 while both advanced, and the ETA read "about 2 hours" for five
hours. A human watching the bar has no signal either.

### 3.4 There are two notions of progress

- `len(self.table.completed)`, read by the watchdog (core.py:481).
- the memlog's node column, written by `MemoryLogger` from `_memory_snapshot`
  (core.py:597) and rendered in the tqdm description (core.py:~1146).

Nothing defines these to be equal, and in §2.4 they behaved differently. This is
the single most consequential gap in the document: with one counter, the incident
would have been classified in minutes.

### 3.5 The invariants are stated precisely, in prose, and checked nowhere

The comments in this engine are unusually explicit about invariants. Four
examples, quoted:

- "Buckets are disjoint, first match wins" — `_memory_snapshot`, core.py:597.
- "Liveness here is an eviction *preference* — never a correctness gate (every
  value is regenerable from lineage)" — liveness.py.
- "A queued write is not reclaimed memory; it is the same memory, twice" —
  `spill`, node_table.py:423.
- "admission only gates NEW work: it cannot reclaim memory already committed to
  bodies that finished computing" — `_reclaim_memory`, core.py:724.

None is executable. Nothing asserts that the buckets partition the live tier,
that `accounted_bytes` equals live plus backlog plus pool (node_table.py:257
documents the identity in prose), or that every resident value sits on exactly one
reclaim queue.

The memlog's `untracked` / `untracked_n` fields (core.py:641) are a hand-rolled
instance of precisely that last check — "resident but on NEITHER reclaim queue — a
leak detector; should stay near zero". They are computed and *displayed*, never
asserted. During the incident `untracked_n` read 26 and nothing happened.

### 3.6 Collaborators are discovered by attribute probing

`hasattr(backend, "set_live_probe")` (liveness.py), `getattr(self.table,
"_persister", None)` and `getattr(persister, "skipped_dead", 0)` (core.py),
`hasattr(self._backend, "put_success_batch")` (persist.py:298). A renamed or
moved method degrades to "feature silently off" rather than failing.

There is no `pyproject.toml` and no type-checker configuration in the repository;
`pytest.ini` is the only tool config, and it sets `addopts = -ra
--strict-markers --maxfail=1`.

### 3.7 Scale-dependent behaviour cannot reach the test suite

`tests/unit/test_memory_backpressure.py` is 1136 lines and is thorough about what
it covers. But it, and the other engine tests, drive real programs through
`ExecutionEngine` with scalar kernels — for example
`test_engine_bounded_scheduling.py` runs `for g in range(0, 400) do g * g + g`.
The behaviour at issue here needs values with a *declared size and cost* — a 4 MB
output from a 135 ms kernel — at thousands of nodes, against a budget small enough
to bite and a writer slow enough to saturate.

Evidence that the gap is load-bearing: the `--sparse-cache` completion-time skip
(commit `016a47e`) passed 1022 unit tests and a 30-case run, then wedged the
369-case run. The defect was one policy line whose consequence appears only when
the live tier approaches the budget.

## 4. Diagnosis

Five defects, in the order in which they would have shortened the incident.

**D1 — Two progress counters, no single source of truth.** (§3.4) Cost: the
incident could not be classified at all. Everything else in this list is
secondary to it.

**D2 — No way to ask a running process what it is doing.** (§2.5) `py-spy`
requires privileges the operator may not have; `_dump_stuck()` exists but is
reachable only when the watchdog raises or `--engine-debug` was set in advance.
An engine whose failure mode is "stops advancing" needs an on-demand dump.

**D3 — Liveness checks are binary, and the in-flight exemption disables them.**
(§3.2, §3.3) A 100× slowdown, and any number of wedged kernels, are both silent.

**D4 — Invariants are unexecutable.** (§3.5) The engine knows what should be true
and cannot check it. `untracked_n = 26` was visible and meaningless because
nothing said what value should make it fail.

**D5 — The test suite cannot reach the regime where these bugs live.** (§3.7)
Consequence: correctness of the resource protocol is established by running large
jobs and seeing what happens, which is what this incident was.

## 5. Proposed changes

Each item states the motivation, the specific design, the cost, the risk, and how
to tell it worked. They are independent; the ordering in §7 is a
recommendation, not a dependency graph, except where noted.

### 5.1 One definition of progress

**Motivation.** D1. Two counters that can disagree make every subsequent
measurement unfalsifiable.

**Design.** Add to `ComputationEngine` a single counter with a documented
meaning — *kernel invocations retired*, incremented in exactly one place, the
completion path at core.py:~991 — exposed as a read-only property
`retired_kernels`. Change three call sites to read it: the watchdog's `cur`
(core.py:481), `_memory_snapshot`'s node field, and the tqdm description. Delete
any other progress arithmetic. Where a second quantity is genuinely wanted
(nodes *interned* versus nodes *retired*), give it a different name and export
both, so that a discrepancy is a fact about the run and not an ambiguity about
the instrument.

**Cost.** Hours. **Risk.** Very low; it is a rename plus a deletion.

**Acceptance.** A test that runs a small program and asserts the watchdog's view,
the memlog's view and the bar's view are equal at the end. And: the two-sample
comparison from §2.3 becomes meaningful, because there is only one thing to
sample.

### 5.2 On-demand state dump, no privileges required

**Motivation.** D2. This is the item that would most have helped during the
incident.

**Design.**

- `faulthandler.enable()` at engine start, unconditionally, so a hard crash
  prints C-level and Python-level stacks.
- A `SIGUSR1` handler installed by `ComputationEngine.run()` that writes to
  `<store-dir>/engine-dump-<pid>-<n>.txt`: every thread's stack
  (`faulthandler.dump_traceback(all_threads=True)`), the existing
  `_dump_stuck()` payload, `_memory_snapshot()`, and the invariant report from
  §5.4 once it exists. Re-entrant and cheap enough to fire repeatedly, so an
  operator can take three dumps a minute apart and diff them — which is exactly
  how "stalled" and "slow" would have been separated.
- A `SIGUSR2` handler that flips `self._debug`, so a run started without
  `--engine-debug` can be made verbose without restarting.

**Cost.** A day. **Risk.** Signal handlers under free-threading need care: the
handler must only set a flag and let the event loop do the writing, or use
`loop.add_signal_handler`. Both are standard.

**Acceptance.** `kill -USR1 <pid>` against a running sweep produces a file
naming the in-flight nodes and their start times. Documented in `results/README.md`
next to the `tail -f` recipe, because the operator reading a stalled bar is the
user of this feature.

### 5.3 Rate-based liveness, and per-kernel deadlines

**Motivation.** D3.

**Design, two parts.**

*Rate.* In `_join_with_watchdog`, keep a ring buffer of completions per interval
(the loop already ticks on `interval = clamp(stall/4, 1, 15)` seconds). Maintain
a trailing median over the last, say, 40 intervals. Emit a `[watchdog] rate
collapse` line — and, in `--engine-debug`, a dump per §5.2 — when the rate stays
below 5% of that median for 8 consecutive intervals. Warn, never raise: a rate
collapse can be legitimate (a phase change from cheap scalars to 3D kernels, as
happens at the start of every sweep in this project), and the engine must not
kill a run over a heuristic. Loudness is the whole ask.

The constants above are a starting point, chosen so that (a) the trailing window
is minutes rather than seconds, so a normal phase change does not trip it, and
(b) the incident's collapse — 340 to at most single digits — clears the 5% bar by
an order of magnitude. They should be revisited against the first ten runs that
trip them.

*Deadlines.* Record `started_at` per in-flight node (a dict written in the
dispatch path, cleared in its `finally`, next to the existing `_dispatch_pins`).
Derive an expectation per node from `compute_ms` already stored for the same
`operator` — median of the last N recorded values for that operator, times a
generous factor (10x), with a floor for operators never seen before (say 600 s).
Replace the `_in_flight > 0` exemption with: *a kernel is overdue if it exceeds
its own expectation*. Then

- one node with a two-hour history gets its two hours;
- fifteen `vox1.mask` invocations, whose recorded median is 135 ms, are overdue
  after seconds, and the watchdog says so.

Raising remains gated on the existing `deadlocked` predicate; overdue kernels
produce a loud report and a dump, not a `RuntimeError`, because a first-ever slow
operator must not be fatal.

**Cost.** Two to three days, most of it in deciding the expectation policy.
**Risk.** False positives on first-run operators; mitigated by the floor and by
warning rather than raising.

**Acceptance.** A test using the harness of §5.5: a kernel deliberately made to
hang while others proceed produces an "overdue" report naming it, within a
multiple of its expectation.

### 5.4 Invariants as executable predicates

**Motivation.** D4, and it is the foundation for §5.5 and §5.6.

**Design.** A new module `voxlogica/engine/invariants.py`, with no dependency on
the engine's mutable state. It defines:

```python
@dataclass(frozen=True)
class Snapshot:
    """An immutable view of engine state, cheap to build, safe to inspect."""
    resident: Mapping[NodeId, int]          # node -> bytes in the live tier
    consumers: Mapping[NodeId, int]
    persisted: AbstractSet[NodeId]
    write_queued: AbstractSet[NodeId]
    evict_candidates: Sequence[NodeId]
    spill_pending: Sequence[NodeId]
    ownerless: Sequence[NodeId]
    dispatch_pins: Mapping[NodeId, int]
    goals: AbstractSet[NodeId]
    incomplete: AbstractSet[NodeId]
    accounted: int
    live: int
    backlog: int
    pool: int
    budget: int
    hard: int
    in_flight: int
    ready: int
    outstanding: int
    sacrifice_ms: float
    compute_ms: Mapping[NodeId, float]
    recomputable: AbstractSet[NodeId]

@dataclass(frozen=True)
class Violation:
    name: str
    detail: str
    witnesses: tuple[NodeId, ...] = ()

Check = Callable[[Snapshot], Violation | None]
```

and a registry of checks. The first set, each corresponding to a prose invariant
already in the code:

| check | states |
|---|---|
| `buckets_partition_resident` | the six buckets of `_memory_snapshot` cover every resident node exactly once |
| `accounted_is_live_plus_backlog_plus_pool` | the identity documented at node_table.py:257 |
| `every_resident_node_is_tracked` | `untracked_n == 0`, i.e. every resident node is on some reclaim queue or explicitly exempt (goal, pinned) |
| `no_value_is_both_dropped_and_expected` | nothing in `resident` is absent from `consumers` while a consumer is pending |
| `progress_is_possible` | `outstanding == 0 ∨ ready > 0 ∨ in_flight > 0` |
| `reclaim_has_an_exit` | if `accounted > budget` then some candidate is durable, or sacrificeable (`compute_ms < sacrifice_ms ∧ recomputable`), or spillable (`backlog < backlog_budget`) |
| `hard_ceiling_respected` | `accounted <= hard` |

`reclaim_has_an_exit` is the wedge stated as a predicate. Whether it can be
violated is the central open question of §6.

**Three consumers, one definition.** (a) In torture mode (§5.6) every check runs
on every maintenance turn and a violation raises. (b) In production the registry
is sampled on the memlog's existing cadence and violations are *logged* with
witnesses — never raised, so a false invariant cannot kill a real run. (c) Tests
call the registry directly.

**Cost.** Building `Snapshot` cheaply is the real work: `_memory_snapshot`
already iterates `table.values`, so the cost is known and acceptable at memlog
cadence, but not on every worker turn — hence torture mode is where the strict
version lives.

**Acceptance.** Each check has a unit test that constructs a violating snapshot
by hand and asserts it is caught. Then: run the existing suite with sampling on
and confirm zero violations, which is itself a result.

### 5.5 A deterministic scheduler harness

**Motivation.** D5. This is the largest item and the one that changes what can be
known.

**Observation that makes it possible.** The scheduler's decisions depend on the
graph shape, value sizes, kernel costs, the budget, and completion order. They do
not depend on SimpleITK. Three seams already almost exist:

- `Executor` (executor.py:66) is already a class the engine is handed;
- `StorageBackend` (storage.py:197) is already an ABC;
- time is read through `time.perf_counter()` and `governor._clock`.

**Design.**

- `tests/harness/fake_executor.py` — computes a node by advancing a virtual clock
  by the node's declared `cost_ms` and returning a value object whose
  `approx_bytes` is the declared `output_bytes`. Values are pure functions of
  their inputs (e.g. a hash), so results are checkable.
- `tests/harness/fake_storage.py` — a `StorageBackend` with a declared write
  bandwidth and an optional failure injector, so writer saturation and
  `spill() -> False` are reachable on demand.
- `tests/harness/virtual_clock.py` — a clock the harness advances, so a run of a
  hundred thousand nodes takes milliseconds of real time. Requires threading the
  clock through `governor` and the watchdog rather than calling
  `time.perf_counter()` directly; this is the one invasive change in the
  proposal, and it is small and mechanical.
- `tests/harness/dag_gen.py` — generators for the shapes that matter: wide
  independent fan-out (the sweep), a deep chain, a diamond, a loop with a
  sequence node consuming every body (the assembly floor that `_reclaim_memory`'s
  docstring is about), and random DAGs with `hypothesis`.

Then property tests, using `hypothesis.stateful` where a schedule matters:

1. **Termination.** For any generated DAG, any budget ≥ the largest single value,
   any write bandwidth including zero, the run completes and every goal settles.
   Zero bandwidth is important: it is `--no-cache`, where the only exits are
   drop-and-recompute and ownerless collection.
2. **Bounded memory.** `accounted <= hard` at every maintenance point.
3. **Correctness under eviction.** Results equal a reference computed with an
   unbounded budget. This is what catches an eviction that strands a value.
4. **No invariant from §5.4 is ever violated.**
5. **No double computation.** `DoubleComputationError` (node_table.py:61) already
   exists as a guard; the harness makes it reachable in a test.

**Cost.** Days, plausibly a week including the clock threading. **Risk.** A fake
executor that diverges from the real one verifies a fiction. Mitigation: keep the
fake trivially simple, and run the *same* property tests through the real
executor on small real programs, so any divergence in scheduler behaviour shows
up as a test that passes on one and fails on the other.

**Acceptance.** The `--sparse-cache` bug of `016a47e` is reproducible as a
failing property test in under a second. That is a concrete, checkable target: it
is the bug that motivated all of this, and if the harness cannot catch it, the
harness is not finished.

### 5.6 Torture mode

**Motivation.** Same as §5.5, but as a cheap continuous check rather than a new
test suite.

**Design.** `VOXLOGICA_TORTURE=1` sets: budget to a few hundred megabytes;
`persist_min_compute_ms` to 0 so everything is written and the writer saturates;
a random 10% failure rate in `spill()`; `_EVICT_SWEEP` to 1 so reclaim is maximally
incremental; and invariant checks to raise rather than log. Then run the existing
suite under it as a second CI job.

**Cost.** A day once §5.4 exists. **Risk.** Flakiness, if any check is wrong;
that is why §5.4 asks for a unit test per check first.

**Acceptance.** The suite passes under torture; a deliberately reintroduced
`016a47e` fails it.

### 5.7 Static checking

**Motivation.** D-adjacent: §3.6. Attribute probing hides interface drift, and
drift is how a "feature silently off" bug is born.

**Design.** Add `pyproject.toml` with mypy configured strict for
`voxlogica/engine/` and `voxlogica/storage.py` first, permissive elsewhere, so the
gate is achievable. Replace probing between modules that ship together with
`typing.Protocol`: `SupportsLiveProbe`, `SupportsBatchWrite`, `SupportsIdIndex`,
`SupportsMaterializedIds`. Keep `hasattr` only at genuine plugin boundaries
(primitive namespaces), where optionality is the contract.

**Cost.** Days, spread. **Risk.** Low, but it will surface existing looseness;
budget for that rather than being surprised by it.

**Acceptance.** `mypy voxlogica/engine` clean in CI, and no `getattr(...,
default)` on an internal collaborator.

## 6. Formal verification

The request was for a complete model, not one targeted question. This section
surveys the options, because the choice depends on what the group wants to prove
and what it is willing to maintain, and then recommends a combination.

### 6.1 What a complete model must cover

The protocol has five interacting parts. A model that omits any of them cannot
answer the question that matters.

1. **Node lifecycle.** `unscheduled → ready → in-flight → complete`, and for the
   value: `resident`, `write_queued`, `durable`, `evicted`, `dropped`, with
   `rematerialize` returning an evicted or dropped value to `resident`.
2. **Admission.** Demand-driven, gated on ready-queue depth, refused above the
   hard ceiling, with the anti-wedge exemption.
3. **Reclaim.** The three passes and four exits of §3.1, including the bounded
   sweep window and the requeue behaviour.
4. **The writer.** A queue with a byte budget, draining at a rate, making values
   durable asynchronously.
5. **The governor.** Pressure derived from RSS, shrinking the budget, ramping
   `sacrifice_ms`, and the `blocking` valve.

### 6.2 Properties worth stating

*Safety.*

- `accounted <= hard` always.
- No value is read after being dropped without an intervening rematerialize.
- Bucket partition: every resident value is in exactly one state.
- No double computation of a node.
- A value with a pending consumer is never lost: it is resident, durable, or
  recomputable.

*Liveness*, under weak fairness on workers, the writer, and the reclaim sweep.

- Every submitted goal eventually reaches `DONE`.
- `over_budget ∧ outstanding > 0` leads to `accounted` decreasing — i.e. no
  permanent wedge. This is `reclaim_has_an_exit` from §5.4, promoted to a
  temporal property.
- No livelock: it is not the case that reclaim runs forever while freeing
  nothing and no kernel retires.

The last two are exactly what the incident could not settle empirically, and they
are the reason to model at all rather than only test.

### 6.3 The options

**TLA+ with TLC, and Apalache.** The default choice for a concurrent resource
protocol. Strengths: temporal properties and fairness are first class, so the
liveness list above is expressible directly; TLC does exhaustive checking of small
configurations (3 nodes, 2 workers, budget 3 units) which is where protocol bugs
actually live; Apalache checks *inductive invariants* symbolically, which scales
past TLC's explicit enumeration and can prove a safety property for all sizes
rather than for a bounded one. **PlusCal** compiles to TLA+ from an algorithmic
syntax much closer to the Python, which shortens the distance between spec and
code and makes review easier for someone who does not read TLA+ fluently.
Weaknesses: real time is awkward, so the watchdog's timeouts model badly; and the
spec is a separate artifact that can drift from the implementation.

**mCRL2.** Process algebra with modal μ-calculus properties, and a toolset built
around exactly the questions here: `mcrl22lps`/`lps2lts` plus deadlock and
divergence detection, with `lts2pbes` for μ-calculus formulas. Deadlock and
*livelock* (divergence — an infinite internal loop making no visible progress)
are primitives of the analysis rather than encodings, which fits the incident
better than TLA+ does. Given the group's formal-methods background this may also
be the least foreign option socially. Weaknesses: quantitative state (bytes,
budgets) must be abstracted aggressively; the community and tooling are smaller
than TLA+'s.

**SPIN / Promela.** Explicit-state model checking of communicating processes,
with deadlock detection and never-claims for liveness. Very good at exactly
"can this set of processes stop making progress". Weaknesses: no natural way to
carry integer budgets without state explosion; Promela's channel-centric idiom
fits an actor system better than an event loop with shared collections.

**UPPAAL.** Timed automata. The right tool for §5.3 specifically: the watchdog's
`stall`, `hard`, `interval` and per-kernel deadlines are real-time properties, and
UPPAAL can answer "is there a run where a kernel is overdue and no report is
emitted" — a question TLA+ answers only by discretising time. Weaknesses: not
suited to the memory protocol; use it for the timeout logic and nothing else.

**Alloy 6.** Relational, with temporal operators since version 6. Excellent for
the *structural* invariants of §5.4 — bucket partition, queue membership,
"exactly one state per resident value" — where the question is about relations
between sets and the counterexamples are small and very readable. Weaker on
fairness and on liveness over long traces.

**P.** A state-machine language whose compiler emits both a model for systematic
exploration and a runtime harness. Its distinguishing feature is that the same
spec can drive the real implementation, so spec drift is caught mechanically.
Attractive here because the engine *is* an event loop plus workers plus a writer,
which maps onto P's machines directly. Weaknesses: the toolchain assumes
C#/Java/C++ hosts; driving a Python implementation from P is possible but is
integration work nobody has done for this project.

**CrossHair.** Symbolic execution of Python functions against contracts. Not a
model checker, but it verifies *the actual code* — and after §5.4 the invariant
predicates and any pure transition functions are exactly the shape it handles
(pure, typed, small). This is the cheapest way to close the model-to-code gap for
the pure parts.

**Nagini (Viper).** Deductive verification of Python with contracts, including
some concurrency. In principle the strongest Python-level option; in practice its
supported subset excludes `asyncio` and much of the standard library, so it would
apply only to carved-out pure modules — where CrossHair already suffices at a
fraction of the effort.

**Hypothesis stateful testing.** Not formal verification, but the executable
counterpart: define the same actions as the model, run them against the real
scheduler, and assert the model's invariants. With a TLA+ or mCRL2 spec in hand
this becomes *conformance testing* — the model says which traces are legal, the
test checks the implementation stays inside them. This is where §5.5 and §6 meet,
and it is the mechanism that keeps a spec from drifting into fiction.

**Deliberately excluded.** Coq, Isabelle and Lean: a full mechanised proof of this
protocol is a multi-month project and the protocol is still changing. Dafny: a
verified reference implementation would then have to be ported to Python by hand,
so the proof would not cover the code that runs. Infer's starvation checker and
CBMC: Java/C/C++ only.

### 6.4 Recommendation

Three artifacts, in this order.

1. **A complete PlusCal/TLA+ specification of §6.1**, checked with TLC on small
   configurations for the full safety and liveness list of §6.2, then with
   Apalache for inductive invariants on the safety half so the results are not
   bounded by the model size. Kept in `doc/formal/engine/`, with a README stating
   which code version it corresponds to and which abstractions it makes — in
   particular how bytes are discretised, since that is where a model can lie
   comfortably.
2. **A UPPAAL model of the watchdog and deadline logic** from §5.3, small and
   self-contained, answering the timed questions the TLA+ model abstracts away.
3. **Conformance tests via `hypothesis.stateful`** in the harness of §5.5, whose
   actions mirror the spec's, so the implementation is checked against the model
   rather than against itself.

If the group would rather work in a process algebra, **mCRL2 substitutes for (1)**
with a real gain: divergence — livelock — is a first-class check there, and
livelock is the thing this incident may have been. That trade is worth discussing
explicitly rather than defaulting to TLA+ because it is the better known name.

Two smaller additions, cheap and worth doing regardless: **Alloy 6** for the
structural invariants of §5.4, because the counterexamples it produces are the
most readable of any tool listed and those invariants are pure structure; and
**CrossHair** in CI over `engine/invariants.py`, because after §5.4 those
functions are exactly what it verifies well, and it verifies the code rather than
a model of it.

## 7. Suggested order

| # | item | effort | why here |
|---|---|---|---|
| 1 | §5.1 one progress counter | hours | every later measurement depends on it |
| 2 | §5.2 SIGUSR1 dump | ~1 day | makes the next incident diagnosable at all |
| 3 | §5.3 rate watchdog + deadlines | 2–3 days | makes it *reported*, not merely diagnosable |
| 4 | §5.4 executable invariants | ~1 week | foundation for 5, 6 and the formal work |
| 5 | §5.5 deterministic harness | ~1 week | the real investment; target is catching `016a47e` |
| 6 | §5.6 torture mode | ~1 day | continuous, cheap, once 4 and 5 exist |
| 7 | §6.4 formal models | weeks, parallel | answers what tests cannot |
| — | §5.7 static checking | opportunistic | alongside everything |

Items 1 and 2 together are the smallest change that would have turned five silent
hours into a diagnosis, and they are worth doing before anything else in this
document is argued about.

## 8. What this document does not claim

It does not claim to know why the run of §2 stopped advancing. The three
hypotheses so far are: memory wedge (contradicted by `write_queued ≈ 0`, and by
§3.1's exits existing), event-loop spin in reclaim (unverified, no stack sample),
and severe slowdown rather than a stop (consistent with zero watchdog lines,
inconsistent with the frozen node counter). §3 onward stands independently of
which is true: each is a property of the code, quoted with a location, and
separately checkable.

## 9. How to review this document

The parts most worth challenging, in order:

- **§4 D1** — is a single progress counter really the top priority, or is that an
  artifact of one person's debugging session?
- **§5.3** — the specific constants (5% of trailing median, 8 intervals, 10x the
  recorded median, 600 s floor). These are guesses with stated reasoning and no
  data behind them yet.
- **§5.5** — is the fake executor a fiction? The mitigation proposed is to run the
  same properties through the real executor; is that enough?
- **§6.4** — TLA+/Apalache versus mCRL2. This is a real fork and the document
  deliberately does not settle it.
- **§6.1** — is the five-part decomposition complete? An omitted part is a model
  that proves the wrong thing.
