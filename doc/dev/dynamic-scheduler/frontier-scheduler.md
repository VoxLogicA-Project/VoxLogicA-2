# The frontier scheduler: O(degree) coordination at any plan size

Status: implemented (2026-07). Companion benchmark: `tests/perf/bench_scheduler.py`.

## Measured results (18-core M-series, disk cache on, ~2ms GIL-releasing kernels)

| plan size | old engine | frontier scheduler |
|---|---|---|
| 50k nodes | 2,579 nodes/s (3.9 busy cores) | 9,201 nodes/s |
| 300k nodes | 686 nodes/s, 439 s wall, 1.5 busy cores, frontier = whole plan (301k) | 6,817 nodes/s, 44 s wall, frontier 36k |
| 1.2M nodes | (extrapolates to ~200 nodes/s, hours) | 5,805 nodes/s, 207 s wall, frontier 36k |

Old per-node cost grew with plan size (−73% throughput from 50k→300k;
extrapolating the curve to the production 9M-node plan reproduces the observed
25–35 nodes/s). New per-node cost is flat: −2% from 300k→1.2M, with an
identical open frontier at both scales. With production-weight kernels (~10ms)
wall time is within 3% of the theoretical minimum (kernel-core-seconds / 18),
i.e. the pool is saturated. A single process now exceeds the aggregate
throughput of the 9-process split (~1,440 nodes/s) by ~4×. Startup: expansion
no longer blocks the event loop — first completion at t≈0 instead of after a
minutes-long single-core DAG-build phase.

## The problem it replaces

The previous engine's per-node coordination cost grew with total plan size, so a
single 9M-node process throttled itself to ~25–35 nodes/s (1–3 of 18 cores busy)
while the identical work split across 9 OS processes ran ~5× faster per core.
Measured causes, in order:

1. **Liveness as tracing GC.** Every 128 completions, `_compute_live_values`
   walked the dependency graph transitively from every incomplete node — O(plan)
   per refresh, and the admission policy (grow the loop window by +1 per worker
   turn while under the memory budget) opened the entire loop, making the
   incomplete set the whole plan. 53% of wall time at 50k nodes; the dominant
   term asymptotically.
2. **N+1 cache membership queries.** `_schedule_subgraph` issued one synchronous
   SQLite `SELECT` per discovered node on the event loop (~0.17 ms each). 28% of
   wall time.
3. **Monolithic loop expansion.** A runtime loop reduced *all* element bodies in
   one synchronous call on the event loop — minutes of single-core DAG building
   before any parallel compute.
4. Unbounded bookkeeping (`_scheduled`, `_dependents`, `_priority`, deps memo)
   never pruned; a hand-rolled second registration path in `_expand` that
   disagreed with `_register` about persisted-but-pruned bodies (latent
   warm-cache deadlock).

## Design principle

**Every piece of scheduler work is O(active frontier) or O(node degree), never
O(plan).** The frontier is what admission lets in; everything behind it has been
reduced to monotone facts (completed set, node specs, disk cache); everything
ahead of it has no state at all.

## Module map (`voxlogica/engine/`)

| module | concern | key invariant |
|---|---|---|
| `graph.py` | dataflow firing + value lifetime (Kahn pending counts, consumer refcounts) | per-node state exists only between `register` and `complete`; every transition is O(degree) |
| `ready.py` | priority ready-queue, memory-parked tier, outstanding-work join | queue is never observed empty while units are outstanding and admissible work exists (progress floor) |
| `admission.py` | chunked loop expansion + demand-driven windows | expansion happens off the event loop, in window-sized chunks paced by ready-queue depth and the live-bytes budget |
| `liveness.py` | O(1) live probe for the disk cache's eviction preference | live ≡ incomplete ∪ (completed with unrun consumers) ∪ loop-pinned; maintained incrementally, no traversal |
| `node_table.py` | hash-consed identity, live tier, persisted-id index | `persisted()` is a set lookup; the index is loaded once and appended on write |
| `persist.py` | non-blocking background writes | unchanged contract; skips re-reading what the index already knows |
| `core.py` | coordination: submit/run, workers, watchdog, queries | single-writer on the event loop; no blocking I/O, no O(plan) step |

## Established techniques used

- **Kahn-style dataflow firing** (pending-dependency counts) — unchanged, it was
  already right.
- **Reference counting instead of tracing** for liveness: the walk was a tracing
  collector run every 128 allocations; refcounts make the same information
  incremental and O(1) to query. (The classic RC/tracing duality, applied to
  scheduler state rather than heap objects.)
- **Demand-signaled backpressure** (Reactive Streams): loop bodies are admitted
  to hold the ready queue inside a target band instead of "grow while under
  memory budget", so the open set tracks *demand*, not plan size.
- **Lazy graph materialization** (as in Dask/Legion task graphs): the DAG of a
  loop is built incrementally, window by window, pipelined with compute.
- **Batch prefetch over N+1 queries** for cache membership.
- **LIFO depth-first tie-breaking** (Cilk's work-first heuristic) — kept from the
  old design: freshly produced values are consumed before new siblings open, so
  intermediates die young and the live tier stays small.

## Peak-memory bounding under real concurrency (2026-07 follow-up)

Status: implemented. Companion benchmark: `tests/perf/bench_scheduler.py
--payload-mb`/`--sequence-floor` (the original bench's kernel returns a tiny
scalar and cannot exercise memory pressure).

### Root cause, confirmed by profiling

A wide runtime-expanded loop over many elements, each producing a large
in-memory value, was suspected to OOM via an *admission burst*: greedy
admission (`accounted_bytes <= budget → admit`) opened an entire window's
worth of elements at once, so peak RSS tracked `window × element size`.
Profiling confirmed this is real for **nested** loops (an outer loop whose
body is itself a loop): `peak_frontier` reached 1351 registered nodes against
a `loop_window` of 18, because the window cap applies independently at each
nesting level and is orthogonal to how eagerly each level fills it.

But profiling a *flat* wide loop whose body values are large and are combined
by an outer `fold`/bare sequence goal exposed a **second, larger, and
previously undiagnosed cause**: value-lifetime refcounting
(`graph.consumers`) pins every completed body's value in the live tier from
the moment it completes until the loop's spliced `sequence` node — which
needs *every* body simultaneously to compute — actually runs. That holds
regardless of the admission window: a loop that admits only one body at a
time still accumulates all completed bodies' bytes, because none of them can
be evicted while the (not-yet-satisfied) sequence node still holds a
consumer reference on each. Peak RSS here scales with **element count**, not
concurrency — and no admission policy can fix it, because admission only
gates *new* work; it cannot reclaim memory already committed to finished
work. This matches the production signature ("healthy throughput right up to
the last log line, then vanished") far better than a burst would: a burst
produces an early spike and a plateau, not a monotonic climb to the end.

Measured (150 elements × 8 MB bodies, single flat loop, `--no-cache`):
`peak_live_mb` = 2400 (a real ~1200 MB floor, doubled by a separate
accounting artifact — see below), sustained from early in the run, while
`peak_frontier` stayed at 19 (i.e. compute concurrency was already properly
window-bounded the whole time; the floor is not a concurrency problem).

### The fix: two complementary, orthogonal valves

1. **Demand-driven (queue-depth) admission** (`admission.py::_has_room`)
   bounds the *burst*. A loop body is admitted only when the ready queue
   would otherwise starve the workers (`qsize < workers`), never merely
   because bytes are under budget. One richly-parallel body can contain
   thousands of nodes and keep every core busy alone, so "bytes available"
   was always the wrong question; "are the workers fed" is the right one.
   Concurrent-element count becomes emergent instead of window-sized. The
   byte budget's soft tier is dropped from this check entirely; the hard
   ceiling remains as a backstop (refuse even while starving, except to break
   a true wedge).

2. **Proactive reclaim of durably-persisted values**
   (`core.py::_reclaim_memory`) bounds the *sequence-assembly floor*. Once a
   completed, still-referenced value has a confirmed durable copy on disk,
   its RAM copy is no longer the only copy, so it is safe to evict early
   under memory pressure — the eventual consumer transparently reloads it via
   the existing `_rematerialize` path (a disk read, never a recompute, since
   only confirmed writes are evicted). Candidates are tracked in a bounded
   FIFO (`_evict_candidates`, capped at 200k entries) and swept in O(1)-ish
   batches (`_EVICT_SWEEP` = 256 per call) from `_maintain`, which already
   runs every worker turn — so this adds no new O(plan) work and is a pure
   no-op when the persister is disabled (`--no-cache`) or the run is
   comfortably under budget.

   Technique: this is **credit-based / durability-gated eviction** — a value
   earns eviction-eligibility once its "credit" (a durable copy) is
   confirmed, the same principle write-back caches use to decide when a
   dirty page may be dropped.

Measured (same 150×8 MB case, disk cache enabled, budget tightened to 300 MB
to force pressure): `evicted_early` = 117/150, sustained RSS plateaus around
480–520 MB instead of climbing to ~1.3 GB, for nearly the whole run.

### The irreducible floor — stated honestly

The fix does **not** eliminate the theoretical peak. `sequence`/`fold` are
not streaming combiners: `executor._compute` builds one kwargs dict from
*all* of a node's dependencies before invoking its kernel, so at the instant
the sequence actually runs, every previously-evicted body must be reloaded
and held simultaneously — a brief, unavoidable `E × body size` spike. A
high-resolution RSS trace (50 ms samples) confirms the shape precisely: flat
~520 MB for the run's whole duration, then a spike to ~1.1 GB in the final
~150 ms when the sequence assembles. The fix converts a floor that was
**sustained for the entire run** into one that is **brief and only at the
very end** — a large reduction in cumulative OOM exposure, not a reduction of
the hard combine-time peak. Eliminating that peak too would require a
streaming/incremental combiner (process bodies in batches with running
eviction instead of materialize-all-then-combine) — a real architectural
change to the `sequence`/`fold` kernel contract, out of scope here, and the
next lever if the terminal spike itself becomes the binding constraint.

A second, minor, safe-direction finding: forwarding a completed loop's value
from its spliced sequence node used to persist the identical bytes to disk a
second time under the loop's own node id, and double-book them in
`live_bytes` (same shared object, two accounting entries). The double-write
is now skipped (`persist=False` on the alias-forward); the live-tier
double-count is a conservative overcount (pushes admission to throttle
*earlier* than strictly needed) and is left as a known, safe artifact rather
than a correctness bug.

A related caution, also confirmed by profiling: under a *severely*
disk-bottlenecked writer (writer throughput far below compute throughput),
proactive reclaim's reload traffic can itself compete with the struggling
writer for disk I/O and make things worse, not better — in that regime the
pre-existing backlog-budget admission throttle (which this change does not
touch) is the valve that actually matters. Reclaim only helps when the disk
can keep up well enough for early eviction to get ahead of pressure.

## Invariants preserved

- Content-addressed dedup; a node is computed at most once per run (`begin`).
- Bounded live memory (parked tier + windowed admission + release-on-last-use).
- Disk-cache reuse across runs, including partial warm caches (the persisted
  index is consulted at registration, one code path).
- Deterministic results: chunked expansion produces identical node ids to
  monolithic expansion (per-element reduction is independent; hash-consing is
  insertion-order-insensitive).
- Deadlock watchdog: same stall/hang semantics, now counting expansion chunks as
  in-flight work.
- `--no-cache` degrades gracefully (no index, no probe, nothing persisted).
