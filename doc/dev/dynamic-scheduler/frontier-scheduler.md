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
