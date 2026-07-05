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

## Next step — real lazy evaluation over sequences

The one place the engine (and `incoming`'s lazy strategy) still does more work
than `main` is `subsequence(result, 0, k)`: both fully expand the outer loop and
compute all N cases, while `main` computes only the demanded k. `main` wins that
comparison purely by laziness, not by a better evaluator.

The right fix is a proper lazy-demand layer over the dynamic DAG: only compute
what a goal actually needs, while keeping the beautiful runtime-expanding DAG.
Treat it like a query engine — a demanded slice/`index`/`fold` pushes its
demand down into the producing loop, so `subsequence(for … , 0, k)` expands and
materializes only elements `0..k-1`. Sequence items are already
content-addressed (`hash_sequence_item`, `complete_item`), which is the hook to
make individual elements independently demandable rather than gated on the whole
sequence node. This should let the engine beat `main` on CPU and memory *as
well as* wall-clock, on the brief's own script. Deferred to the next pass.
