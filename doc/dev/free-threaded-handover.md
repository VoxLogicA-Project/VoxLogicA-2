# Free-threaded (no-GIL) Python — handover + results

Status: **Phases 0-2 DONE and PASSED** (2026-07, on the Mac dev box + fmt-5000
Linux/24-core/RTX PRO 5000 remote). Target branch: `feat/pointwise-fusion`.
Implements issue #27. Read that issue first; §5 below is the actual outcome
and supersedes §0's original acceptance criteria — read that first if you're
deciding what to do next, then the rest of this doc for how the validation
was done and why it's trustworthy.

## 0. Objective & acceptance criteria

Break the ~50%-core GIL ceiling (measured: 24-core box sustains ~12 cores /
~1100% CPU with the ready queue holding 100+ and `in_flight` pinned — work is
available, cores idle). Run the *same* engine on a free-threaded CPython build
so the cheap pointwise kernels and per-node Python dispatch run genuinely
parallel.

Done when:
1. Engine runs on free-threaded 3.14t with numpy/SimpleITK/numba importing.
2. **Byte-identical results** vs the GIL build on the same programs, and the
   unit suite is green on BOTH builds.
3. On a pointwise-heavy benchmark, sustained core utilization is materially
   above 50% (target: approach physical core count).
4. Any synchronization added is ~zero-cost on the normal GIL build.

## 1. The concurrency model (READ THIS — it defines the whole task)

The engine is **single-writer / multi-reader by construction**, and asyncio's
event loop stays single-threaded even under no-GIL. Concretely:

- **All mutation of shared engine state happens on the event-loop thread**:
  `NodeTable.set_value / complete / begin / evict / complete_without_value`,
  and every graph map (`_pending`, `consumers`, `_dependents`, `incomplete`,
  `_dispatch_pins`, `live_bytes`). These are called only from the worker
  *coroutine* in `engine/core.py` (`_dispatch`/`_finish`/`_reclaim_memory`),
  never from a pool thread.
- **Pool threads only READ** `table.values[dep_id]` and `table.nodes[node_id]`
  (see `engine/executor.py::_compute`, the `lambda dep_id: table.values[dep_id]`
  read closure). They compute and *return* a value; the event loop writes it.

So the only loop↔thread data crossing is: **event loop writes `table.values`
while pool threads read `table.values`.** Within that:

- **Adds are safe**: a dep is `complete()`d (its key inserted) *before* any
  consumer is dispatched, so the key a pool thread reads is already present and
  is not being inserted concurrently.
- **Removals are the only hazard**: `_reclaim_memory` (event loop) can `evict`
  a value a pool thread is mid-read on. **This is already guarded** by
  `_dispatch_pins` (`core.py:164-180, 455-482, 710/766`): the loop pins a
  dispatch's deps before submitting and unpins in `finally`; `_reclaim_memory`
  skips anything pinned. That guard is a logical guard and remains correct
  under no-GIL.

On CPython 3.14t, built-in `dict`/`set`/`list` individual operations are
memory-safe (per-object locking / critical sections) — a concurrent get vs
set/resize will not corrupt the interpreter. So **no explicit lock is required
for the individual `table.values` get/set**; correctness rests on the logical
single-writer discipline + the existing pin guard, both of which hold.

### 1a. The two things that DO need checking (small, enumerated)

1. **Kernel-level shared module state written on pool threads.** Kernels run on
   pool threads, so any module-global a kernel writes is a genuine multi-thread
   write. Known cases in `primitives/vox1/kernels.py`:
   - `_BASE_IMAGE` — already guarded by `_BASE_IMAGE_LOCK`. ✓
   - `functools.lru_cache` wrappers (`_snake_cached`, `_hyperrectangle_cached`,
     `_hyperrectangle_numba_faces_cached`) — `lru_cache` is thread-safe. ✓
   - numba `@njit` dispatchers — compile-on-first-call is internally locked by
     numba. Validate under load in Phase 2, don't pre-lock.
   Action: grep every `primitives/**/*.py` for `global ` and module-level
   mutable state; confirm each is either lock-guarded, `lru_cache`, or
   write-once-before-parallel. Report the list.
2. **`arrays.py::PolyArray` lazy view caches.** A `PolyArray` can be read by
   multiple pool threads (a shared dep feeding several consumers). Its lazily
   built, cached views (`.np()`, `.sitk()`) must be safe under concurrent first
   access. Check `arrays.py` for its view-cache locking (there is threading
   import + a lock there already — confirm every lazy build path takes it).

That is the entire audit surface. Everything else is single-writer.

## 2. Phased plan

### Phase 0 — Free-threaded environment (mechanical)

Create a SEPARATE venv; do not touch the working `.venv`.

```bash
cd /Users/vincenzo/data/local/repos/VoxLogicA-2
uv python install cpython-3.14.4+freethreaded
uv venv --python 3.14.4+freethreaded .venv-ft
# MINIMAL engine deps only — do NOT install torch/nnunet/dask/fastapi/
# playwright/mcp for this experiment; they are unrelated to the compute path
# and may lack cp314t wheels, which would block you on irrelevant builds.
uv pip install --python .venv-ft \
  "lark==1.2.2" "numpy>=2.3.2" "numba>=0.66.0" "llvmlite>=0.48.0" \
  "SimpleITK>=2.5.5" "canonicaljson==2.0.0" "typer==0.16.0" \
  "pydantic>=2.12" "tqdm" \
  "pytest==8.4.0" "pytest-mock==3.14.1" "hypothesis==6.151.9"
```

Note: `requirements.txt` pins `numba==0.64.0` and `SimpleITK==2.5.2` — those
have NO free-threaded wheel; the floors above (0.66.0 / 2.5.5) are the first
that ship cp314t. When this phase succeeds, update those two pins (guarded by a
`python_version` marker if the GIL build must stay on the old ones).

Gate: all three import and report a build.
```bash
PYTHONPATH=implementation/python .venv-ft/bin/python -c \
 "import sys,sysconfig,numpy,numba,SimpleITK as s; \
  print('GIL disabled build:', sysconfig.get_config_var('Py_GIL_DISABLED')); \
  print('GIL on at runtime:', sys._is_gil_enabled()); \
  print(numpy.__version__, numba.__version__, s.Version.VersionString())"
```
Expect `Py_GIL_DISABLED = 1` and `GIL on at runtime: False`. If a C-ext force
re-enables the GIL at import (numba historically may), it prints True — RECORD
THIS, it means numba is holding the GIL and is a benchmark caveat, not a
blocker (the pure-numpy fallback path still parallelizes).

### Phase 1 — Correctness smoke (go/no-go)

Run the engine unit suite on BOTH interpreters; compare.
```bash
# GIL build (baseline)
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit \
  -k "engine or fusion or cache or scheduler or numba or poly or sequence" -q \
  > /tmp/ft_gil.txt 2>&1
# free-threaded build
PYTHONPATH=implementation/python .venv-ft/bin/python -m pytest tests/unit \
  -k "engine or fusion or cache or scheduler or numba or poly or sequence" -q \
  > /tmp/ft_nogil.txt 2>&1
diff <(grep -E "passed|failed|error" /tmp/ft_gil.txt) \
     <(grep -E "passed|failed|error" /tmp/ft_nogil.txt)
```
Skip/deselect any test importing torch/nnunet/dask/fastapi (not installed in
`.venv-ft`). If a test fails ONLY under no-GIL, that is a real race — capture
which test and STOP (see §3 escalation) unless it maps to a §1a site.

### Phase 2 — Concurrency stress (surface races the unit suite misses)

Run a pointwise-heavy program many times under high `--threads` on the ft
build; results must be byte-identical across runs and vs the GIL build. Reuse
`tests/perf/bench_scheduler.py`. Add a small stress loop:
```bash
for i in $(seq 1 20); do
  PYTHONPATH=implementation/python .venv-ft/bin/python \
    implementation/python/voxlogica/main.py run <pointwise.imgql> \
    --threads 24 --no-cache 2>/dev/null | sha256sum
done | sort | uniq -c   # MUST be a single hash
```
Compare that hash to the GIL build's. Divergence or crash → a race; localize
to a §1a site if possible, else escalate.

### Phase 3 — Guards, ONLY if Phase 1/2 fail at a §1a site

Apply the minimal guard at the exact failing site. Patterns:
- Kernel module-global write: wrap in an existing/`threading.Lock()` like
  `_BASE_IMAGE_LOCK`. Zero-cost on GIL build (uncontended lock).
- `PolyArray` view cache: ensure the lazy-build path takes the instance lock
  and double-checks the cache after acquiring (double-checked locking).
Do NOT add locks anywhere the audit says is single-writer — that only
serializes and defeats the goal.

### Phase 4 — Benchmark the ceiling

On the 24-core remote (pc-ciancia), full pointwise-heavy sweep, ft vs GIL:
capture sustained %CPU (e.g. `pidstat`/`top -b`), nodes/s, wall-clock. Target:
>50% cores, approaching 24. Record in `doc/dev/engine-optimization-results.md`.

## 3. STOP-AND-ESCALATE rules (for the implementing model)

STOP and hand back to a strong model / the user if ANY of:
- A test/stress failure that does NOT map to a §1a site (unknown race).
- The fix would require a lock on a structure §1 calls single-writer.
- numba fails to import or force-re-enables the GIL AND removing numba changes
  results (should not — it's a fallback — but verify).
- A dependency in the minimal set has no cp314t wheel and would need building
  from source.
Never guess at synchronization. A wrong lock is worse than no lock here:
it silently serializes and the benchmark will look like "no-GIL didn't help",
sending everyone down the wrong path.

## 4. Explicitly OUT of scope (do not do)

- Do NOT replace asyncio with a hand-rolled ThreadPoolExecutor scheduler. The
  parallelism model is already threads + shared graph + pool; no-GIL just
  unlocks it. (Earlier analysis that framed asyncio callbacks as the
  bottleneck was based on a misread cProfile cumulative dump — cumulative time
  in `select`/`_run_once` is the loop *parked waiting on workers*, not
  overhead.)
- Do NOT install torch/nnunet/dask into `.venv-ft`.
- Do NOT change node ids, hashing, or the DAG — no-GIL is a runtime policy, not
  a plan rewrite.

## 5. RESULTS — what actually happened, and what's next

Phases 0-2 were executed for real (not just planned), on both the Mac dev box
and fmt-5000 (Linux, 24-core, RTX PRO 5000 Blackwell 48GB — a different host
than §2 Phase 4 names; that section is superseded by this one). All passed:

- **Phase 0**: `.venv-ft` built on both hosts from cp314t wheels — numpy 2.4.6,
  numba 0.66.0/llvmlite 0.48.0, SimpleITK 2.5.5, dask 2025.5.1 (pure-Python;
  at the time, pulled in transitively by `execution_strategy/__init__.py` via
  the now-removed dead `ParallelExecutionStrategy` — see §5c — dask itself is
  still a real dependency of `dask_map`/sequence-slicing, just no longer
  imported at package-import time for a strategy nothing selected). No source
  builds needed anywhere. SimpleITK and numba both auto-re-enable the GIL on
  import unless overridden with `PYTHON_GIL=0`; confirmed genuinely disabled
  under the override on both hosts.
- **Phase 1**: 63-66/66 comparable unit tests pass identically GIL vs no-GIL
  (delta is only tests skipped for missing optional deps — torch/nibabel —
  in the minimal `.venv-ft`, not real failures).
- **Phase 2**: stress test (200-consumer fan-out over one 50MB value, tight
  memory budget forcing 14-18 proactive evictions per run — the exact
  `_dispatch_pins` race window) ran 20/20 byte-identical at 2x thread
  oversubscription under genuine `PYTHON_GIL=0`. §1's single-writer analysis
  holds.
- **Phase 4 (superseded — see below)**: on fmt-5000, the GIL build sustained
  ~12.7/24 cores (1270% CPU) on the real `brats017_full.imgql` sweep — this
  reproduces issue #27's ceiling almost exactly. Free-threaded reached
  ~23.4/24 cores (2340%) on the same workload — the ceiling genuinely breaks.

### 5a. But profiling revealed the ceiling wasn't the whole story

`perf record` (sampling, kernel-level — works under free-threading where
`py-spy` currently does NOT: it looks for a "GIL-holding thread," a concept
that doesn't exist here, and fails with `failed to get gil_thread_id` even on
a trivial script, on the latest py-spy 0.4.2 — don't retry it until upstream
adds free-threaded support) on the live 24-core run showed:

- **SimpleITK's own C++ kernels: 46-77% self-time** (varies by sample window)
  — the dominant cost, always. Not the Python interpreter (5-24%), not numba
  (~1%, confirming fusion's cones are cheap and not the bottleneck).
- **Kernel/futex wait+wake: 14-36% of ALL cycles** — a NEW cost that only
  shows up under free-threading. Root cause: SimpleITK/ITK spawns its own
  internal worker-thread pool per filter call (the `Pool` threader this repo
  already configures in `kernels.py` to avoid a SIGSEGV — see
  [[voxlogica-dt-segfault-fix]]), uncapped by default. That stacks on top of
  the engine's own 24-thread pool — two independent parallelism layers
  fighting over the same 24 cores, synchronizing via locks neither knows
  about the other.
- **Fix, verified A/B on the real BraTS pipeline (30-60 real cases,
  `ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1` vs unset)**: kernel/futex self-time
  dropped from ~15-17% to ~4.2% (a ~3.5-4x cut in contention), and wall-clock
  improved ~9% (43s → 39s on a 30-case slice) — a free win, one env var, no
  code change needed. **Not yet wired into the engine's own init** (the
  natural place is next to the existing `SetGlobalDefaultThreader("Pool")`
  call in `primitives/vox1/kernels.py`) — do that before any further
  free-threading work, it's the cheapest lever available.

### 5b. Ranked next steps for the actual 10x goal (not just "beat the GIL ceiling")

With SimpleITK compute now confirmed as the dominant, uncontaminated cost
(75%+ once ITK's own threading is capped), here's what has real leverage,
ranked by confidence/effort — **not yet acted on, needs a decision**:

1. **Shrink the sweep itself.** `brats017_full.imgql`'s `chBest` runs
   `hgrid`(3)×`fgrid`(5)×`Rgrid`(5)×(`Ogrid`(2)|`Kgrid`(3))×2 channels ≈ 900
   parameter combos PER CASE, brute-force. A smarter search (coarse-to-fine,
   or dropping a grid dimension that barely moves the oracle) hitting similar
   Dice with ~100 combos would be a 9x cut in total work — bigger than
   anything below, free of engine risk, and orthogonal to it. This is a
   research/algorithm question, not an engineering one — ask before assuming
   the full grid is load-bearing.
2. **Extend fusion beyond strictly-elementwise ops to fixed-neighborhood ops**
   (`dilate`/`erode`/`smoothen` — called constantly inside the sweep) via
   numba, leaving only genuinely global ops (`grow`'s region-growing,
   `maxvol`'s likely connected-component selection, `fill_holes`) on
   SimpleITK. Real reach into the 75% slice fusion currently can't touch
   (`fusion.py`'s "a cone never crosses a SimpleITK op boundary" rule was
   written for elementwise ops specifically; this is a proposal to widen that
   boundary, not violate it — needs the same bit-identical property-test
   discipline the existing elementwise ops use).
3. **GPU via `cupy`/`cucim`**, NOT hand-written `numba.cuda` kernels — verified
   (not assumed) that `cupyx.scipy.ndimage` binary morphology (dilate/erode/
   fill_holes) is genuinely N-dimensional (3D volumes fine) and `cucim`'s
   `distance_transform_edt` has explicit, working 3D kernels (not a generic
   any-D implementation — 2D and 3D are separately hardcoded; a 2D-specific
   bug at >1024px doesn't affect our 3D case). The design work still needed:
   keep volumes GPU-resident across the ~900-combo sweep per case (mirroring
   the CPU "cone" idea — transfer once, pull back only the scalar Dice per
   combo), since per-call host↔device transfer would eat the win otherwise.
   CPU-numba reimplementation of these same ops was considered and rejected —
   real compute doesn't change, ITK's C++ is already competitive, and it risks
   recreating the oversubscription problem via a second layer of intra-kernel
   parallelism.

None of 1-3 above have been implemented. fmt-5000's `looping_experiment`
`_scratch/` has throwaway smoke-test artifacts (`brats017_smoke{4,8,30,60}.imgql`
and matching `.db`/`.db.files`) from this validation work — safe to delete,
not meant to be kept.

### 5c. The `main` vs `incoming` A/B benchmark (2026-07-28)

A separate, simpler benchmark than the oracle sweep above: run the *same*
TACAS'19-style FLAIR threshold+grow procedure to completion on the *full*
`BraTS_2019_HGG` set (259 cases) on both branches, and compare wall-clock and
Dice. Unlike §5's sweep (6 cases × a 75-combo grid), this exercises one pass
per case — closer to a real end-to-end run than a parameter search.

**Location (untracked by design — `looping_experiment/` is excluded via
`.git/info/exclude`, not `.gitignore`; it is fmt-5000-only working state, not
part of this repo's history):**
`fmt-5000:~/data/local/repos/VoxLogicA-2/looping_experiment/ab_compare.sh`

**How to run it** (one command; safe to re-run, cleans its own state):
```
ssh fmt-5000 'cd ~/data/local/repos/VoxLogicA-2/looping_experiment && ./ab_compare.sh'
```
It builds a `main` worktree at `/tmp/vl2_main_baseline` on first run, generates
two bench `.imgql` files (see gotcha below), runs `main` single-arm then
`incoming` with `--threads 24`, and writes a summary to
`_scratch/ab_compare_results.txt`.

**Gotcha that cost a full debugging cycle: `border` is not one primitive.**
`main` has a 0-arg `border` (implicit image); `incoming` requires
`border(img)` explicitly (commit 5c26781). A single shared `.imgql` cannot
satisfy both arities, so the script generates *two* bench files
(`bench_tacas19_full_main.imgql`, `bench_tacas19_full_incoming.imgql`),
identical except for that one call. The first run of this benchmark silently
mis-set this up (one shared file, incoming's arm crashing instantly with
`StaticAnalysisError: Invalid arity for 'border'`); fixed by generating both.

**Results — three states of the same benchmark:**

| state | main | incoming | ratio | Dice |
|---|---|---|---|---|
| shared bench file (bug) | 180s, exit 0 | **crash**, exit 1 | — | 0.82485 (main only) |
| fixed (per-arm bench files), pre zero-copy | 179s / 742% CPU (7.4 cores) | 54.6s / 2209% CPU (22.1 cores) | 3.37x | 0.82485 (both, identical) |
| post zero-copy audit (this session) | 179s / 742% CPU | **20.8s** / 2220% CPU | **8.61x** | 0.82485 (both, identical) |

Dice is bit-identical (`0.8248473289639666`) across every state that
completed — the engine and the zero-copy changes are both purely
performance-affecting, never correctness-affecting, on this benchmark.

**Why 3.37x, not ~24x, was the first (wrong) alarm, and what it actually was.**
The naive read — "24 threads should give ~24x" — is wrong on two counts, one
a measurement bug on our side and one a real property of `main`:

1. `main` was never a true single-core baseline: `/usr/bin/time -v` showed
   742% CPU (7.4 cores) — ITK's *own* internal filter threading, uncapped,
   already parallelizing `main`'s "single-threaded lazy evaluator." Against a
   genuine 1-core baseline, `incoming` (pre-zero-copy) was already ~24.8x;
   3.37x is the ratio of two *already-parallel* numbers
   (22.1/7.4 × the ~11% work reduction from fusion ≈ 3.35x, matching the
   observed 3.37x to within 1%).
2. A first attempt to blame ITK itself for the shortfall (a threaded
   microbenchmark claiming 24 threads were *slower* than 1) was a **self-made
   measurement bug**: unequal total work between arms, image generation
   inside the timed region, and `np.random.rand`'s global lock being
   mistaken for an ITK lock. A corrected version (equal work, pre-generated
   images, per-thread RNGs) showed ITK ops scale 9-13x at 24 threads
   (`SignedMaurerDistanceMap` 10.1x, `ConnectedComponent` 11.9x, `Not` 13.3x)
   — ITK is not the bottleneck, and the earlier "0.31x" conclusion was
   retracted in the same session it was reported.

**`--profile`'s own numbers were, separately, not trustworthy** — see the
tracking issue [voxlogica-project/voxlogica-2#34](https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/34)
and the warning now printed by `EngineExecutionStrategy.run()`. The tell was
`asyncio.base_events._run_once` cumtime of 1389s inside a 52s wall-clock run,
and aggregate `tottime` across all 2236 functions summing to ~51.8s — deceptively
close to the 52s wall time, which briefly and *wrongly* read as evidence of
serialization. `/usr/bin/time -v`'s single `%CPU` number was what actually
answered the question; a per-thread-aware profiler would be needed to trust
the full trace.

**What zero-copy actually fixed** (2.63x wall-clock on top of the above,
20.8s vs 54.6s, at unchanged `kernels_executed=11143` and unchanged CPU%):
the `vox1`/`arrays`/`geom` kernels called `sitk.GetArrayFromImage` (always
copies) and unconditional `sitk.Cast` (copies even when the pixel type
already matched) throughout, instead of `arrays.py`'s existing zero-copy
`PolyArray` machinery. Type-guarding the casts alone cut a no-op `Cast` from
10.65ms to 0.6µs (~17,000x) on a BraTS-sized volume. Along the way, a real
correctness hazard surfaced and was fixed: `GetArrayViewFromImage` does
**not** keep its source image alive — reading a zero-copy view after its
source image is garbage-collected silently returns garbage (verified: NaN),
not an exception. `arrays.pinned_view()` closes that hole by keeping the
source referenced from the view. See the commits on `incoming`:
`primitives: make sitk/numpy conversions zero-copy by default` and the
preceding dead-code removal (`remove orphaned Svelte UI and dead
ParallelExecutionStrategy (dask)`, unrelated to zero-copy but surfaced by the
same audit).

**Combined effect vs `main`: 8.61x** (179s → 20.8s), all of it now genuine
parallelism plus reduced work — no further headroom from more threads at
92%+ core utilization; the only remaining lever is further work reduction.

**Correction (2026-07-28, later the same day): the 20.8s figure above does
not reproduce.** Three independent re-runs of the identical 259-case
benchmark, same code, same host, idle box, gave 47.83s / 48.03s / 49.16s —
consistent with each other, inconsistent with the single 20.79s
measurement this section's "8.61x" was built on. The 20.79s run was an
outlier (cause unconfirmed — thermal state, scheduler luck, transient load
are candidates, not verified); **the true post-zero-copy number for this
benchmark at `--threads 24` is ≈48s, not 20.8s, so "8.61x vs `main`" should
be read as ≈3.7x** until re-verified with multiple runs. The zero-copy work
itself is not in question (bit-identical Dice, real code-level improvement,
independently reasoned) — only this one aggregate multiplier. See §5d
below for why 24 threads was the wrong number to measure against in the
first place.

### 5d. The thread-count ceiling is memory bandwidth, not the scheduler — and is now auto-detected

Chasing the discrepancy against a reference implementation (a from-scratch
VoxLogicA1 A/B/C comparison, out of scope for this doc) turned up something
with a direct, immediately-actionable consequence for every benchmark number
in this file: **the engine's own default thread count (`os.cpu_count()`,
i.e. 24 on fmt-5000) is the worst of the operating points tested**, and the
reason is memory bandwidth, confirmed three independent ways rather than
assumed.

**The host is hybrid, not 24 uniform cores.** `lscpu`/sysfs: 8 P-cores
(Intel Core Ultra 9 285K, up to 5.8\,GHz) + 16 E-cores (up to 4.6\,GHz), one
thread per core, no SMT. Pinning identical single-threaded ITK kernel calls
to a P-core vs.\ an E-core via `taskset` measured the E-core penalty
directly: 0.55–0.78x a P-core's throughput across `Cast`, `GreaterEqual`,
`Not`, `Mask`, `SignedMaurerDistanceMap`, `BinaryDilate`,
`ConnectedComponent` — mean ≈0.70x. Real capacity is
$8 + 16\times0.70 \approx 19.2$ P-core-equivalents, not 24. Every
"parallel efficiency" percentage computed anywhere earlier in this
investigation (including §5a) used a 24-equal-core denominator and is
wrong by this factor.

**STREAM (copy/triad, swept 1–24 OpenMP threads) found the actual DRAM
bandwidth ceiling: 69.5\,GB/s at 8 threads, declining to 65.7\,GB/s at 24.**
Bandwidth scales only 1.39x from 1→8 threads — nothing on this box can
exceed that ratio if genuinely bandwidth-bound.

**The real benchmark, swept over `--threads` (40-case subset), gives the
actual curve rather than an assumption:**

| threads | wall (s) | CPU% | CPU-sec | RSS | speedup |
|---|---|---|---|---|---|
| 1  | 54.03 | 106%  | 49.7  | —       | 1.00x |
| 8  | 9.12  | 742%  | 58.7  | 1.96 GB | 5.92x |
| 10 | 7.95  | 909%  | 63.1  | 2.20 GB | 6.80x |
| 12 | 7.50  | 1046% | 69.0  | 2.61 GB | 7.20x |
| 14 | 6.90  | 1236% | 75.5  | 2.89 GB | 7.83x |
| 16 | **6.85**  | 1384% | 84.4  | 3.12 GB | **7.89x — optimum** |
| 18 | 7.06  | 1543% | 98.0  | 3.28 GB | 7.65x |
| 20 | 7.26  | 1712% | 112.5 | 3.69 GB | 7.44x |
| 24 | 7.84  | 2011% | 144.9 | 4.38 GB | 6.89x |

Confirmed at full scale (259 cases, unrestricted): 16 threads = 38.61s /
542 CPU-s; 24 threads = 47.67s / 1010 CPU-s; 8 threads = 52.45s / 368
CPU-s. Dice bit-identical at every thread count tested.

**Read the whole curve, not its endpoints.** A first pass swept only
1/2/4/8/16/24, read "24 is bad" as "only P-cores are good", and shipped a
P-cores-only default (commit 9eef749) — which is **33% slower than the
optimum and 16% slower than the `os.cpu_count()` default it replaced**. The
finer sweep above shows both extremes are wrong: E-cores contribute real
throughput (8→16 threads buys 25% wall-clock for 44% more CPU), they are
simply not free. Fixed in 49b630a. Note also that peak RSS scales linearly
with thread count (1.96→4.38 GB) — see the decoupling experiment below for
why that is, and why it is not the binding constraint.

**`perf stat` isolates the mechanism as memory stalls, not lock
contention:** sys time rises only 1.38x from 8→24 threads (contention would
spike sharply); P-core IPC collapses 2.80→1.66; cache-miss rate rises
42%→64%. Converting misses to DRAM traffic: ≈26\,GB/s demand at 8 threads
(37% of the 69.5\,GB/s ceiling — headroom) vs.\ ≈75\,GB/s at 24 (114% of
the 65.7\,GB/s ceiling at that thread count — oversubscribed). A DSO-level
`perf report` breakdown at both thread counts shows `_SimpleITK.so`'s share
of self-time is unchanged (≈90%) at 8 vs.\ 24 — the extra cost at high
thread count is the same C++ compute taking longer per call, not a shift
toward Python glue.

**Retraction of an earlier reading in this same investigation:** a small
(160³-voxel) microbenchmark of individual ITK ops showed 9–15x scaling at
24 threads and was read as "these ops are compute-bound with headroom."
That's inconsistent with the measured 1.39x bandwidth ceiling — the
microbenchmark's small working set fit comfortably in cache and never
exercised the DRAM pressure that 40–259 concurrent full BraTS volumes do.
Ranking transferred; magnitude did not. Caught only by re-measuring on the
real workload rather than trusting the synthetic proxy — see §5a-§5c above
for the earlier instances of the same lesson.

**Is the working set the real limit? No — tested, and it isn't.** Peak RSS
scales linearly with thread count because `engine/config.py` floored the
loop-admission window at `max_concurrency`: every added worker opens another
concurrent loop body (another whole BraTS case, several 36MB volumes),
against a 36MB L3. To test whether *that*, rather than the E-cores, capped
useful concurrency, `VOXLOGICA_LOOP_WINDOW` is now honoured **below** the
worker count (previously it was silently floored, so the experiment was
impossible to run):

| config | wall | CPU% | CPU-sec | RSS |
|---|---|---|---|---|
| threads=16, window=16 (default) | **6.94s** | 1370% | 85.0 | 3.25 GB |
| threads=24, window=8  | 7.37s | 1250% | 81.7 | 3.43 GB |
| threads=24, window=12 | 7.60s | 1750% | 121.1 | 4.23 GB |
| threads=24, window=16 | 8.10s | 1941% | 144.8 | 4.40 GB |

Decoupling **does** recover part of the 24-thread loss (8.10s → 7.37s, CPU
145→82) but **under-feeds the pool** — 1250% means only ~12.5 of 24 cores
busy, because 8 open bodies don't expose enough ready nodes for 24 workers
— and never reaches the 16-thread number. So the coupling is a real effect,
not the binding constraint: **useful concurrency for this workload tops out
at ~14–16 simultaneous kernels however it is configured.** Past that it is
the memory system, not the scheduler and not the admission window.

**Fix shipped** (9eef749, corrected by 49b630a): `engine/topology.py` reads
the kernel's P/E split from `/sys/devices/cpu_core/cpus` and defaults to
**P-cores + half the E-cores** ("balanced" — 16 here, the measured
optimum). Falls back to plain `os.cpu_count()` anywhere that sysfs path
doesn't exist (macOS, non-hybrid CPUs, cgroups without it), so the change
is never worse than the prior default and never raises. `--threads N`
always wins outright. `--threads-auto {balanced,p-cores,logical}` selects
the heuristic when `--threads` is `0`: `p-cores` is retained as a real
choice (2.5x less CPU and RSS for ~33% more wall-clock — correct on a
*shared* box, wrong on a dedicated one), `logical` restores the plain
count. Also settable via `VOXLOGICA_THREADS_AUTO`. The resolved
`max_concurrency` is now always in the run's JSON summary
(`cache_summary.max_concurrency`), so the auto-picked value is never
silent. Verified end-to-end with no `--threads` flag: auto-picks 16,
**6.81s** — matching the swept optimum (6.85s).

**Net result vs VoxLogicA1** (same 40 cases, same host): VL1 8.17s,
`incoming` 6.81s — **17% faster**, having previously been 9% slower with
the P-cores-only default. Note that all 24 cores (7.84s) also still beats
VL1; the point of "balanced" is that it beats VL1 by more.

**Caveat, stated as plainly as the fix:** every mode here is a heuristic
fitted to one host's saturation point on one workload's memory-access
pattern, not a general law. A memory-light workload, a box with more DRAM
channels, or a non-hybrid CPU will saturate somewhere else. `--threads N`
remains the correct answer for anyone who has measured their own case. The
curve is flat between 14 and 18 (within 3%), so the cost of being *roughly*
right is small — the cost of landing at either extreme is 14–33%.

### 5e. Should thread count be tuned dynamically? (open, not done)

The optimum is set by DRAM bandwidth saturation, which is a property of the
*host* and the *workload's* access pattern — so a static heuristic fitted
to fmt-5000 is portable only by luck. Three approaches, in rising order of
cost and risk; **none implemented**:

1. **Offline calibration, cached.** A `voxlogica calibrate` subcommand runs
   the thread sweep once per host, writes the winner to a config file, and
   the default reads it. Honest, zero runtime overhead, no scheduler risk,
   and it produces exactly the evidence a user needs to trust the number.
   Cost: minutes, once. **This is the recommended next step if portability
   matters.**
2. **Startup probe.** Run a STREAM-like bandwidth probe at process start
   and derive the thread count. ~1–2s of overhead on every run — acceptable
   for a 259-case sweep, absurd for a 7-second one. Only sensible if gated
   on expected run length, which is not known up front.
3. **Runtime adaptation.** Gate dispatch with a semaphore (the pool itself
   can't resize) and tune its permits from observed node-completion rate.
   Principled and portable, but it touches the scheduling contract §1
   describes as single-writer and delicate — and `admission._has_room`
   already uses `ready.qsize() < workers` as its demand signal, so the
   gate and the admission logic would have to stay consistent or admission
   will over-open loop bodies for workers that are being held back. Real
   oscillation risk. **The measured upside is small** — the curve is flat
   within 3% across 14–18 — so this buys portability, not peak speed, and
   should not be attempted for the latter.

The honest summary: dynamic tuning is worth it for *not being wrong on an
unknown machine*, not for squeezing this one. Option 1 gets most of that
benefit for a fraction of the risk.
