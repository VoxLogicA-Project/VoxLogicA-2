# Task: optimize the `incoming` computation engine

## Goal

On the `incoming` branch of VoxLogicA-2 there are two execution strategies for
running an ImgQL work plan: the default `lazy` strategy and an opt-in live
computation engine (`--engine`, in `implementation/python/voxlogica/engine/`).
Both currently produce correct results but are slower and far more
memory-hungry than the older `lazy` strategy on `main` for the same
computation.

**Your goal:** make the `incoming` engine at least match, and ideally beat,
`main`'s `lazy` strategy on the BraTS threshold-sweep workload — in wall-clock,
CPU-seconds, and peak memory — while keeping results byte-identical and all
tests passing. This is a routine software performance-engineering task on a
data-analysis pipeline (medical-image segmentation research). Nothing here is
security-, bio/chem-, or ML-training-infrastructure related.

## Baseline measurements (this machine, macOS, 18 cores)

Workload: threshold sweep shaped like `tests/threshold_sweep.imgql` — a nested
runtime for-loop, `for path in paths do ... for th in thresholds do
volume(BinaryThreshold(img, th, hi, 255, 0))`. Measured on 10 BraTS-sized
(155×240×240) volumes × 100 thresholds. All three strategies produced
byte-identical numeric output.

| strategy | wall-clock | CPU-seconds (user) | peak RSS |
|---|---|---|---|
| `main` (lazy) | 1.54 s | 11.6 s | 241 MB |
| `incoming` (lazy) | 1.65 s | 24.4 s | 984 MB |
| `incoming` (`--engine`) | 1.72 s | 25.4 s | 8.5 GB |

So the `incoming` architectures spend roughly 2× the CPU and up to ~35× the
memory of `main` for the same answer. The extra cost is architectural
overhead, not useful work.

## Root cause (hypothesis to verify, not gospel)

`main`'s lazy strategy walks the DAG with direct recursive Python calls;
parallelism comes only from ITK's internal per-op threading. `incoming`
instead **materializes every unrolled loop iteration as a tracked DAG node**
(one node object, dependency-graph entry, future, and thread-pool dispatch per
`(case, threshold)` pair). When each iteration's real kernel
(`BinaryThreshold` + `volume`) is cheap relative to that per-node bookkeeping,
the bookkeeping dominates. The `--engine` path is worse still on memory because
its content-addressed live tier retains materialized values pending eviction,
and with the default `--memory-mb` (60% of RAM) nothing is forced out at this
scale — so values accumulate.

Confirmed: this is **not** ITK/Python thread oversubscription (capping
`ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1` did not change the CPU cost).

## Directions to explore

Treat these as leads, not a checklist. Measure before and after each change;
keep whatever wins and revert whatever doesn't.

1. **Don't eagerly materialize thousands of node objects.** The DAG should act
   as the computation *queue*, not a fully-instantiated in-memory graph.
   Consider generating/streaming ready work lazily rather than building the
   whole expanded node set up front.
2. **Let completed values be reclaimed.** Once a node's last consumer has run,
   drop the strong reference so Python can garbage-collect it — investigate
   weak references / `weakref.WeakValueDictionary` for the value tier, and make
   sure nothing (progress tracking, completed-set bookkeeping, closures,
   result lists) pins old values alive. The 8.5 GB RSS strongly suggests
   values are being retained long past their last use.
3. **Reduce per-node scheduling overhead.** Profile the hot path in
   `engine/core.py` (`_schedule_subgraph`, `_register`, `_finish`, `_worker`)
   and `execution_strategy/lazy.py`. Look for per-node dict/set churn,
   redundant hashing, or O(n) scans that scale with node count.
4. **Right-size parallelism.** For cheap kernels, an 18-way thread pool with a
   per-task dispatch cost can be net-negative. Consider batching cheap
   iterations, a work-stealing / chunked approach, or a dask-style scheduler —
   and expose tuning so cheap-kernel sweeps don't oversubscribe.
5. **Keep the lock-free, single-writer event-loop design** already in the
   engine (all scheduling state is mutated on one asyncio loop; only kernels
   run off-thread). Improve throughput without reintroducing locks.
6. **Symbolic vs eager expansion.** Eagerly expanding a runtime loop into N
   concrete nodes is the expensive step. Explore expanding lazily/symbolically
   — materializing a node only when a worker is actually about to run it.

## Constraints (must hold)

- **Correctness:** output must stay byte-identical to `main`'s `lazy` strategy
  on every test workload. Verify by diffing the printed results.
- **Tests:** `implementation/python` unit + integration tests must still pass
  (`.venv/bin/python -m pytest`). The engine's own 18 unit tests under
  `tests/` must stay green.
- **No behavior/semantics change** to the ImgQL language.
- Keep both strategies available; `lazy` stays the default.

## How to benchmark (real data)

Real BraTS data is on this machine:

- BraTS 2020 training: `~/data/local/datasets/MICCAI_BraTS2020_TrainingData`
  (369 cases, uncompressed `*_flair.nii`)
- BraTS 2019 training: `~/data/local/datasets/MICCAI_BraTS_2019_Data_Training`

Point a copy of the threshold-sweep script at one of these (glob
`*_flair.nii`), run under `/usr/bin/time -l` to capture wall-clock, user CPU
seconds, and maximum resident set size, and compare `incoming` (both
strategies) against `main`. Run isolated experiments in parallel when they
don't contend for the same cores/memory, and collect results together. Redirect
long output to files rather than into the terminal; the printed `result=[...]`
line and the `/usr/bin/time` summary are what matter.

Suggested invocation:

```bash
PY=/Users/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python
PYTHONPATH=implementation/python /usr/bin/time -l \
  "$PY" implementation/python/voxlogica/main.py run <sweep>.imgql --no-cache [--engine]
```

## Deliverables

1. Code changes on this `engine-optimization` branch that measurably reduce the
   `incoming` engine's wall-clock, CPU, and peak memory on the BraTS sweep,
   ideally beating `main`'s `lazy` numbers above.
2. A short results table (same format as the baseline) showing before/after.
3. All tests passing.
4. A brief note of what worked, what didn't, and why.

See `INCOMING_FEATURES.md` for the full feature catalog of `incoming`, and
`doc/dev/unified-computation-engine.md` for the engine's design and invariants
(especially: readiness is gated on *completion*, never value residency, so
eviction can only ever cost a recompute — preserve that property).
