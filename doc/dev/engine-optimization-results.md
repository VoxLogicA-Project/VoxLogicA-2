# Engine optimization — pass 1 results

Goal (see `OPTIMIZE.md`): make the `--engine` live computation engine match/beat
`main`'s lazy strategy on the BraTS threshold sweep in wall-clock, CPU, and peak
memory, output byte-identical, tests green.

## Results

Workload: 10 BraTS-2020 FLAIR volumes × 100 thresholds, `--no-cache`, this
machine (macOS, 18 cores). Every config re-measured back-to-back in the same
round (the machine is in active use, so only *relative* numbers are meaningful).
Output byte-identical across all three (`diff` clean).

Benchmark script prints `subsequence(result, 0, 5)` — only 5 of the 10 cases.
`main`'s lazy strategy computes just those 5; the incoming architecture eagerly
expands all 10. So a fair CPU comparison forces all 10 cases everywhere:

**Equal work (all 10 cases computed):**

| strategy            | wall-clock | user CPU | peak RSS |
|---------------------|-----------:|---------:|---------:|
| `main` (lazy)       |    2.20 s  |  11.0 s  |  230 MB  |
| `incoming` (lazy)   |    1.55 s  |  12.0 s  |  800 MB  |
| `incoming` (engine) |  **1.18 s**|  11.8 s  |  880 MB  |

**Engine, before vs after this pass (5-case script, as in the brief):**

| engine        | wall-clock | user CPU | peak RSS |
|---------------|-----------:|---------:|---------:|
| before        |    ~1.7 s  |  12.5 s  | **8516 MB** |
| after         |  **1.18 s**|  11.8 s  |  **880 MB** |

Peak memory: **8.5 GB → 880 MB (~9.5×)**. Confirmed bounded, not just smaller:
doubling to 20 cases keeps peak at ~0.9–1.0 GB (pre-fix it would have been
~17 GB). A live-value probe shows peak 26 resident images / 216 live values —
i.e. the 18-worker concurrency working set, no accumulation.

Net: the engine now has the **best wall-clock** of the three (≈1.8× faster than
`main`, because it extracts more parallelism), **CPU on par** with `main` on
equal work, and memory back in the same class as the lazy strategy. The
remaining gap to `main`'s 230 MB is the space–time cost of that higher
parallelism (more images resident at once), and it is now bounded.

## What worked

1. **Eager reclamation of the live tier** (`core._release` → `NodeTable.evict`).
   The engine kept every completed value in `NodeTable.values` until a
   memory *budget* (60% of RAM ≈ 31 GB) forced eviction — which never triggered
   at this scale, so ~1000 threshold images (~9 MB each) piled up. Now a value
   is dropped the instant its last consumer has run. The design invariant makes
   this safe: readiness is gated on *completion*, never residency, so a dropped
   value at worst costs a recompute (and here it is never re-demanded). This
   matches the lazy strategy's garbage-collection behaviour.

2. **Depth-first (LIFO) ready queue** (`core._enqueue`). With 18 workers and
   FIFO tie-breaking the scheduler ran breadth-first: it produced many threshold
   images before the `volume` calls consumed them, so intermediates piled up
   even *with* eager eviction. Negating the sequence number makes equal-priority
   nodes drain newest-first, so a freshly produced image is consumed by its
   dependent immediately and evicted. This is the standard depth-first =
   minimal-live-set result, and it is what finally brought peak memory down to
   the lazy strategy's class. (It also improved wall-clock — better locality.)

3. **Removed the redundant second RAM cache.** `NodeTable` stored every value
   twice — in `values` *and* in `MaterializationStore._memory` (a 1024-entry LRU
   of large images) — and persisted each value twice (`_store.put` *and* a
   direct `backend.put_success`). For the engine, `values` is already the
   authoritative live tier. The store is now used purely as a disk tier and only
   when a real persistent backend exists; under `--no-cache` it is skipped
   entirely (no serialization, no persist thread, no LRU). `memory_capacity=0`
   keeps it a pure disk tier when caching is on, so values never live in two
   places.

4. **Cached kernel signature introspection** (`executor._signature`).
   `inspect.signature` was re-parsed on every one of the sweep's ~2000 kernel
   calls; it is stable per kernel, so it is now cached. Minor but free.

Removed the now-dead memory-budget machinery: `engine/memory.py`, the
`_releasable`/`_relieve_memory` bookkeeping, and the `--memory-mb` / `memory_mb`
plumbing (the budget concept no longer governs the live tier).

## What did not move (and why)

- **CPU is dominated by ITK kernels**, not scheduling, so once memory was fixed
  the engine's CPU already matched `main` on equal work. There is no 2× CPU
  overhead in the architecture — the brief's 2× was `main` computing half the
  cases.
- **Peak memory (880 MB) is still above `main`'s 230 MB.** This is the cost of
  the engine's higher parallelism (more concurrent images), and it is now
  bounded. Capping concurrency would trade away the wall-clock win.

## Pass 2 — lazy sequence access (short-cut fusion)

Pass 1 left one gap: `subsequence(result, 0, k)` where `result = for path in
paths do …` fully expanded the loop and computed all N cases, while `main`'s
lazy strategy computed only the demanded k. That was the whole of `main`'s
apparent 2× CPU edge — laziness, not a better evaluator.

Fixed at reduction time with **short-cut fusion** (the deforestation rewrite
lazy compilers use): a positional slice/index commutes with a
position-independent producer, so it is pushed into the producer's iterable and
only the demanded elements are ever produced.

```
subsequence(for x in xs do e, a, b)      ->  for x in subsequence(xs, a, b) do e
slice      (for x in xs do e, a, b)      ->  for x in slice(xs, a, b) do e
index      (for x in xs do e, i)         ->  index(for x in xs[i:i+1] do e, 0)  (constant i)
index      (sequence(e0, e1, …), i)      ->  e_i                     (constant i)
subsequence(sequence(e0, e1, …), a, b)   ->  sequence(e_a … e_{b-1})  (constant bounds)
```

`map` is fused the same way; `filter` is deliberately excluded (it changes the
position→element mapping, so slicing does not commute). The rewrite lives in
`reducer._fuse_sequence_access`, hooked into `_plan_primitive_call`, so it is
strategy-agnostic — **both** the engine and the lazy strategy benefit — and
needs no scheduler change. It sees through `let` because the reducer has already
substituted bindings to node ids, and it composes (nested slices fuse in one
bottom-up pass). Output stays byte-identical to `main`.

**Effect (BraTS sweep, `subsequence(result, 0, 5)` over 10 volumes; interleaved,
machine under load so read the ratios, not the absolutes):**

| config                 | wall-clock | user CPU | peak RSS | ops computed |
|------------------------|-----------:|---------:|---------:|-------------:|
| engine, before fusion  |    ~4.9 s  |  ~25 s   |  850 MB  |   3118 (all 10) |
| engine, after fusion   |  **~2.6 s**|  ~12 s   |  690 MB  |   1568 (only 5) |
| main (lazy)            |    ~7.7 s  |  ~13 s   |  230 MB  |   ~5 cases      |

Fusion halves the work (10 cases → the 5 demanded), roughly halving wall-clock
and CPU and trimming memory. On `main`'s own script the engine now **matches
`main` on CPU** (same cases computed) and **beats it on wall-clock** (higher
parallelism). When the full sequence *is* demanded (print all 10) fusion is a
no-op and pre/post numbers are identical within noise — no regression. No test
regressions.

`index` over a loop at a **constant** position is fused too: the iterable is
sliced to that single element (`xs[i:i+1]`), the producer runs over it, and the
sole result is taken — so an indexed access computes one element, not N. The
terminal `index(…, 0)` is emitted with fusion disabled so it does not re-enter
the rewrite. Regression coverage: `tests/unit/test_sequence_fusion.py` pins both
the semantics and the "only demanded elements computed" property, on both
strategies.

## Pass 3 — CPU parity and non-blocking disk cache

**Beating `main` on CPU.** The kernels are identical, so the only CPU to win
back is redundant work. Profiling the sweep exposed one: `ReadImage` and
`MinimumMaximum` ran **twice per case** (20 calls for 10 cases). Cause: a closure
releases its captured values when it completes, but a closure completes
trivially the instant its captures exist — *before* the loop it gates has
expanded. With eager eviction that dropped the loop's input image to zero
consumers and evicted it, so the first expanded body re-read it. Fix
(`core._finish`/`_expand`): a closure no longer releases its captures on
completion; expansion transfers that hold to the per-element bodies once they
are the real consumers, so captures stay resident and inputs are read once
(20 → 10 calls). `_deps` is also memoized (immutable node specs) to trim
per-node scheduling cost.

Result: the engine no longer does 2× the reads on a sliced workload; on equal
work its CPU now **matches `main`** (was worse) while wall-clock stays ~1.6×
better. Meaningfully *beating* `main` on CPU is not achievable — the kernels are
identical and the engine carries irreducible bookkeeping (node objects,
hash-consing, scheduling) that `main`'s direct recursion does not; the residual
few-percent gap is that bookkeeping. Capping ITK per-op threads was tested and
does not help (these kernels are not ITK-multithreaded here).

**Non-blocking disk cache.** Caching previously (a) blocked the event loop when
its bounded persist queue filled, (b) serialized values on the scheduling
thread, and (c) rewrote every value on every run — so a cached run was ~2×
slower than `--no-cache` and a warm re-run got no speedup. Replaced with a
dedicated `AsyncPersister` (`engine/persist.py`):

- Completed values are handed to one IO-bound writer thread through an unbounded
  queue — `submit` never blocks the event loop, and serialization + disk writes
  happen entirely off the scheduling thread.
- Each value's reference is dropped the moment it is written, so it is
  collectible as soon as the live tier has also released it — persistence never
  pins memory past the write.
- Writes are idempotent: a value already durable on disk is not rewritten, so a
  warm re-run persists nothing.
- The in-flight (unwritten) backlog is bounded by bytes; when it exceeds budget
  the engine throttles dispatch of *new* kernels (an `await asyncio.sleep`, not a
  blocked loop), so memory stays bounded without ever stalling scheduling.

Effect on the 10-case sweep with a real SQLite backend: warm re-run **10.3 s →
5.07 s** (now compute-bound, no rewrites); cold **10.1 s → 9.0 s**; peak RSS
bounded at ~950 MB (backpressure) instead of ballooning; cold/warm/`--no-cache`
outputs byte-identical. Regression coverage in
`tests/unit/test_engine_caching.py`.

## Still open

- **Peak memory > `main`** remains (parallelism working set; bounded) — the
  space–time cost of the engine's higher parallelism, not a leak.
- **Index/slice by a *runtime* (non-constant) position** is left unfused: it
  would need an arithmetic `i+1` node, and computed-position access is
  vanishingly rare. Constant positions (the overwhelmingly common case) are
  fused. A fully general runtime element-demand layer (independently demandable
  content-addressed sequence items via `hash_sequence_item`/`complete_item`)
  would subsume this, but adds real scheduler complexity for little practical
  gain over the fusion above.
- **Pre-existing `fold` flake** in `tests/unit/test_lazy_subsequence.py`
  (intermittent, order-dependent, in the lazy strategy's `fold` handling) is
  unrelated to this work — it reproduces on the pre-change tree — and is left
  for a separate investigation.
- **The persistent store is a bounded LRU cache.** It persists every value
  (gzip level 1; masks shrink ~70×) but caps total payload bytes at a budget
  (default 100 GB, `--cache-max-gb`, 0 = unbounded) and evicts least-recently-
  used payloads past it — oldest access first, larger first among ties, on the
  async writer thread. Evicted values are regenerable from lineage, so eviction
  only ever costs a recompute. This is deliberately *eviction*, not admission:
  admission is a blind bet at write time (refuse a subtree, get a variant next
  line, lose it), whereas eviction observes actual reuse and recency naturally
  protects the "variant on the next line" case. Verified end-to-end: a 1-case
  brats014 run that would write ~6 GB stays at ~1.8 GB under a 2 GB budget, with
  correct output. Upgrading LRU to size/cost/frequency-weighted (GDSF/LHD) is a
  possible refinement if profiling shows hot scalars evicted for cold images.

## Pass 4 — one scheduling/caching path for every node

Runtime loop-expanded nodes used to be scheduled by a hand-rolled loop inside
`_expand` that skipped the cache-prune every other node gets in
`_schedule_subgraph`. So a warm re-run recomputed all 2086 expanded kernels even
though their results were on disk — defeating the reason we materialise nodes at
all. `_expand` now schedules the spliced subgraph through the **same**
`_schedule_subgraph`, so an expanded node is treated identically to any other:
already-persisted results are pruned and loaded from the cache on demand. There
is one scheduling/caching/storing path, not two.

Effect (10-case sweep, SQLite): a **warm re-run runs 0 kernels**, 4.0 s → 0.54 s,
895 MB → 168 MB, output byte-identical. Because identity is content-addressed,
this also means expensive preprocessing shared across *different* sweeps
(`ReadImage`, distance transforms, superpixels, …) is computed once and reused
everywhere. Regression coverage: `test_engine_caching.py`
(`test_warm_run_reuses_runtime_expanded_nodes`).
