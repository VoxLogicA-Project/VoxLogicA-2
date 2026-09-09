# The fix: hand the writer an alias, not a copy — −11% wall, −3% energy, −7% peak RSS

**Target.** `AsyncPersister.submit`'s payload copy is 94–96% of
`NodeTable.complete`, 1.2–1.9 ms of the single event loop per completion, and
injecting delay into that path showed it is worth about 5 s of wall clock per
millisecond (`../2026-09-09-why-workers-wait/`). Two earlier attempts to relieve
it failed on energy: relocating the copy to the worker raised CPU-seconds 2.6%
and recomputes 37% for no wall gain (`../2026-09-09-copy-location-closed/`).

**Why the copy existed.** A fresh kernel value is a `PolyArray` holding the
`sitk.Image`; `.np()` builds a zero-copy read-only alias of ITK's buffer.
SimpleITK images are copy-on-write, so `GetArrayViewFromImage` can call
`MakeUnique`, which reallocates and frees the buffer another thread is reading —
observed as a SIGSEGV through `arrays.pinned_view`. The copy bought the writer a
buffer that no COW could move.

**The change.** Build that numpy view ONCE, on the worker, at the moment the
image is freshly created and unshared — where `MakeUnique` is a no-op — and let
`PolyArray` cache it. Every later reader, the writer thread included, then gets
the same read-only alias and nothing triggers copy-on-write again. `submit` hands
that alias to the writer (`_payload_alias`) and falls back to copying only if
there is no cached view. **The race is removed rather than paid for**, and the
alias costs no memory.

**Inputs.** The fixed mixed task, 20 BraTS cases, `--threads 32`, cold store per
run, host `fmt-5000` idle. Three repetitions each. All six runs 16 of 16 goals
with `dice_best_mean = 0.8513501430954795`. Baseline `836357b`.

## Result

| variant | wall (3 reps) | mean | **CPU-s** | **CPU ms/compl** | kernels | recomputes | peak RSS | loop occupancy |
|---|---|---|---|---|---|---|---|---|
| copy (baseline) | 28.6, 27.4, 26.1 | 27.4 s | 502 | 32.96 | 14,984 | 562 | 12.4 GB | 67% |
| **alias** | 25.4, 24.0, 23.5 | **24.3 s** | **486** | **31.80** | 15,019 | **501** | **11.5 GB** | **40%** |

Every guard the method sets is satisfied, and in the same direction:

- **Wall −3.1 s, −11.3%**, and the distributions do not overlap: the alias
  variant's worst run (25.4 s) beats the baseline's best (26.1 s).
- **Energy down, not up**: −16 CPU-seconds (−3.2%) and −1.16 CPU-ms per
  completion (−3.5%). This is the test both earlier attempts failed.
- **Work unchanged**: 15,019 kernels against 14,984, 0.2% apart.
- **Recomputes down 11%** (562 → 501) and **peak RSS down 0.9 GB (−7%)** —
  because a copy was removed rather than added. That is precisely what
  distinguishes this from the relocation attempt, which kept the alias AND made
  a copy, held two buffers per value, and paid for it in evictions.
- **Loop occupancy 67% → 40%**, which is the mechanism showing up directly.
- **`dice_best_mean` identical to sixteen digits** on all three runs.

Predicted magnitude, from the injection slope of ~5 s per millisecond of loop
cost: the loop shed about 0.48 ms per node (27 points of occupancy), so ~2.4 s
was expected and 3.1 s measured. Same order, slightly better.

## The safety argument, stated because it is the whole risk

The alias is only safe while nothing triggers SimpleITK's copy-on-write on that
image after the view is cached. Three things make that hold, and each is a
property of the engine rather than a hope:

1. **Values are immutable.** They are content-addressed; a kernel produces a new
   image rather than mutating an input, and `PolyArray` marks an aliasing numpy
   view `_readonly_np`.
2. **The view is built at the one safe moment** — on the worker, before the
   value is published to the node table, when nothing else can hold a reference
   to that image.
3. **It is cached**, so no later reader repeats `GetArrayViewFromImage` and no
   later reader can therefore trigger `MakeUnique`.

`_payload_alias` returns None unless the cached view exists AND is a contiguous
read-only alias, so any value that does not meet the conditions still takes the
copying path. Three runs of the sweep and the full suite are the evidence that
the argument holds; a crash here would be a SIGSEGV, which is loud.
