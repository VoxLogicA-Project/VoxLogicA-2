# incoming — feature catalog

`incoming` is an integration branch that stacks five merged PRs on top of
`main` (as of commit `a2caf6b`, "vox1: add slic and label_mean region
primitives"). It has not been merged to `main`. This file lists what it adds,
in landing order. For the full narrative/handoff history see
[INCOMING-LOG.md](INCOMING-LOG.md); for the new engine's design see
[doc/dev/unified-computation-engine.md](doc/dev/unified-computation-engine.md).

## 1. Two-level result cache (#17, #18)

Fixes the lazy interpreter dropping image-like values behind a node-id
placeholder, which caused shared subtrees to recompute on every demand under
`--no-cache`. Adds in-run RAM memoization, bounded by a memory/disk two-tier
LRU cache.

- `implementation/python/voxlogica/storage.py`
- Tests: `tests/unit/test_inrun_memo.py`, `tests/unit/test_two_level_cache.py`

## 2. Async task-graph executor (#19)

Replaces the recursive single-threaded DFS evaluator with an `asyncio` +
`ThreadPoolExecutor` executor, so ITK kernels (which release the GIL) run
concurrently instead of one at a time.

Required `border(img)`, `x(img)`, `y(img)`, `z(img)` (namespace `vox1`) to
take an **explicit** image argument — they used to read an implicit global
"current base image," which broke once node evaluation order stopped being
deterministic under concurrent execution.

Hardening that landed alongside it: exceptions propagate instead of being
swallowed; closures/constants are never cached; concurrency and memory are
bounded via backpressure, frontier eviction, and depth-first LIFO worker
ordering, so the executor can't OOM on a large run.

## 3. Static loop expansion (#20 / #21, "plan-expansion")

`for` loops over a constant-length iterable are unrolled into independent DAG
nodes at reduce time — each iteration becomes its own parallelizable,
individually-cacheable node. Capped by `--for-expansion-cap` (default 4096;
`0` disables).

Also: user-defined operators may now shadow the primitive alias table.

## 4. Dynamic loop expansion (#22, "dynamic-expansion")

`for` loops whose iterable is only known **at runtime** (e.g. `dir(...)`,
`MinimumMaximum`-derived thresholds) are expanded into DAG nodes the instant
the iterable's value materializes, splicing new nodes into the *live* running
schedule. Includes fixes for nested dynamic expansion (ready-gating, capture
pinning) and rematerializing evicted nodes referenced by spliced loop bodies.

This is the mechanism behind `tests/threshold_sweep.imgql`'s nested
`for path in paths do ... for th in thresholds do ...` now producing one
tracked DAG node per (case, threshold) pair instead of being evaluated by
plain recursive Python calls.

## 5. Live computation engine (#24, "unified-execution")

A new, **opt-in** evaluator (`voxlogica/engine/`) that coexists with the
proven `lazy` strategy, enabled via `--engine`:

- `NodeTable` — content-addressed (Merkle) node identity, tiered values, a
  no-double-computation guard.
- `ComputationEngine` — priority scheduler with submit/await/prioritize.
- `Executor` — pure `inputs -> value` per primitive, thread-pool backed.
- `Expander` — single-semantics expansion (reduction only; no separate
  runtime AST interpreter).
- Deadlock-free scheduling and memory-pressure eviction (readiness gated on
  completion, never residency, so eviction only ever costs a recompute).

Files: `implementation/python/voxlogica/engine/{core,executor,expander,memory,node_table,priority,query,strategy}.py`.

Validated (per the design doc): single/multi-query evaluation, nested runtime
loops, real-data oracle parity with `lazy`, 18 unit tests, and forced
1 MB / 300 MB live-tier eviction+rematerialize correctness. Also exercised in
this session's threshold-sweep benchmark (see caveats below): `--engine`
produced results byte-identical to both `lazy` strategies, but on this
cheap-kernel workload it was the most expensive of the three — see the numbers
below.

## 6. CLI: flags replace env vars (commit `3b4330f`)

The final commit on `incoming` removed all execution-tuning env vars in
favor of `voxlogica run` flags, and added per-instance thread control:

| Removed env var | Replacement flag |
|---|---|
| `VOXLOGICA_ENGINE=1` | `--engine` |
| `VOXLOGICA_MAX_CONCURRENCY` (and similar) | `--threads N` (default: CPU count) |
| `VOXLOGICA_DYNAMIC_EXPANSION` | `--dynamic-expansion` / `--no-dynamic-expansion` (default: on) |
| `VOXLOGICA_FOR_EXPANSION_CAP` | `--for-expansion-cap N` (default 4096; `0` disables) |
| `VOXLOGICA_ENGINE_MEMORY_MB` | `--memory-mb MB` (default: 60% of system RAM) |
| `VOXLOGICA_ENGINE_DEBUG=1` | `--engine-debug` (dumps the stuck node frontier on failure) |

Storage-tuning env vars (e.g. `VOXLOGICA_MEMORY_CACHE_CAPACITY`) and the
nnU-Net env vars are unchanged.

## 7. Live progress reporting

A tqdm progress bar now runs during `voxlogica run`, with its total growing
live as static/dynamic expansion splices new nodes into the schedule
(`nodes: N/M`, current op label).

## 8. New `vox1` primitives (commit `a2caf6b`)

Added for the `looping_experiment` `brats013` region-atom study:

- `vox1.slic(image, grid_spacing, spatial_weight)` — SLIC superpixel/
  supervoxel segmentation (wraps `sitk.SLIC`); accepts scalar or vector
  (multimodal) images.
- `vox1.label_mean(label_image, value_image)` — paints each label with the
  mean of `value_image` over that label; a piecewise-constant homogeneity
  quotient over an over-segmentation.

## Known characteristics / caveats

- **Dynamic expansion has real per-node overhead; `--engine` costs more
  still.** A head-to-head benchmark of `main` vs `incoming` on a
  threshold-sweep workload (nested runtime for-loops, identical to
  `tests/threshold_sweep.imgql`'s shape) found byte-identical numeric results
  across all three strategies, but the `incoming` architectures were markedly
  more expensive for the same computation. On 10 BraTS-sized
  (155×240×240) cases × 100 thresholds:

  | strategy | wall-clock (CLI) | CPU-seconds (user) | peak RSS |
  |---|---|---|---|
  | `main` (lazy) | 1.54 s | 11.6 s | 241 MB |
  | `incoming` (lazy) | 1.65 s | 24.4 s | 984 MB |
  | `incoming` (`--engine`) | 1.72 s | 25.4 s | **8.5 GB** |

  (On 20 tiny 64³ cases the ordering is the same; `incoming` lazy/engine are
  ~1.9× slower wall-clock than `main` there.) Materializing every loop
  iteration as a tracked, thread-dispatched node isn't free, and that fixed
  cost isn't amortized when each iteration's actual kernel
  (`BinaryThreshold`+`volume`) is cheap. The `--engine` path additionally
  holds far more in RAM at once (8.5 GB vs `main`'s 241 MB, ~35×) because its
  content-addressed live tier retains materialized values pending
  eviction. See memory note `incoming-branch-threshold-sweep-perf` for the
  full numbers. The parallelism/resumability/cross-query-sharing payoff is
  expected to matter for expensive per-iteration kernels, repeated overlapping
  queries, or large case counts under a tuned `--memory-mb` budget — none of
  which this benchmark stresses.
- `doc/dev/unified-computation-engine.md` referenced the removed
  `VOXLOGICA_ENGINE=1` / `VOXLOGICA_ENGINE_DEBUG=1` env vars; fixed in this
  pass to reference `--engine` / `--engine-debug`.
- `doc/dev/modules/execution.md` still describes a `DaskExecutionStrategy` /
  `StrictExecutionStrategy` split that doesn't exist on either `main` or
  `incoming` (current strategies are `SequentialExecutionStrategy` and
  `LazyExecutionStrategy`) — this predates `incoming` and is out of scope for
  this catalog, but is worth a follow-up cleanup.
