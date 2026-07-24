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
  numba 0.66.0/llvmlite 0.48.0, SimpleITK 2.5.5, dask 2025.5.1 (pure-Python,
  pulled in transitively by `execution_strategy/__init__.py`, harmless to
  install). No source builds needed anywhere. SimpleITK and numba both
  auto-re-enable the GIL on import unless overridden with `PYTHON_GIL=0`;
  confirmed genuinely disabled under the override on both hosts.
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
