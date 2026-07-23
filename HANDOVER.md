# HANDOVER — Kernel Fusion (Phase 2 leg 2 done → next: exploit numba's real ceiling)

Branch: `feat/pointwise-fusion`. Last committed work: `9bdcd99` (Stage B wired).
Working tree since then has the *revision batch* (size-gate move, vector guard,
tests, docs, `bench_numba_fusion.py`) — **not yet committed**. First action for
the executor: commit it (message at the bottom of this file).

This project ran on a two-model protocol (Fable = designer/reviewer, Sonnet =
implementer). You are continuing it with **GPT as SUPERVISOR** and **Cursor as
EXECUTOR**. Same division of labor, described below.

---

## 0. The single most important fact

**numba gives ~200× on the kernel math, and we are capturing only 3–5× of it.**

Measured directly (128³ float32, 6-op elementwise chain):
- one sitk kernel chain per voxel-loop iteration: **3.41 ms**
- the equivalent numba-compiled flat loop: **0.01 ms** → ~**300×** faster (call it 200× to be conservative across dtypes/sizes).

But end-to-end speedup is only **1.5–3.6× (128³)** and **5.26× (BraTS 240×240×155, depth-40 chain)**. See `doc/dev/dynamic-scheduler/frontier-scheduler.md` §"Phase 2 leg 2".

**Why the 200× collapses to 5×** — three per-loop-element costs the compiled kernel does not touch (Amdahl):

1. **numpy→sitk exit copy (the biggest leak).** A Stage-B cone outputs a
   `PolyArray` built from numpy — it has **no cached `sitk` view**. The next
   consumer that isn't itself elementwise triggers `Executor._unwrap` →
   `PolyArray.sitk()`, a full O(voxels) copy. Worse: `_unwrap` converts
   **every** `PolyArray` argument to sitk *unconditionally* — including the
   loop's sequence-assembly step, which merely collects values into a Python
   list and never needs them as images. So every exit pays an image-sized copy
   that nothing consumes as an image.
2. **The ~1 ms per-element scheduler floor** — body reduction + admission +
   completion bookkeeping. This bounds Stage A identically; fusing kernels
   harder cannot beat a floor that isn't kernels. (Same tax previously
   recorded as "no wall-clock win on cheap-kernel sweeps".)
3. **Input view building + output wrapping** per cone.

At depth 12 the *theoretical* end-to-end ceiling is already only ~3× — the
200× kernel win is dividing an ever-smaller slice of wall time.

**Where the 10–20× the user expected actually lives** (impact order — this is the backlog):

### Lever A — Lazy / kernel-aware `_unwrap` (cheapest high-impact move)
Stop converting numpy→sitk at every cone exit. Stay in numpy across cone
boundaries **and** across sequence assembly; convert to sitk **only** when a
genuinely non-elementwise sitk kernel actually consumes the value. This is
already `arrays.py`'s stated design intent ("a chain of fused kernels should
stay in numpy end-to-end and only pay this once"). Kills leak #1.
- Touch points: `Executor._unwrap` (make it demand-driven, not eager),
  `engine/strategy.py` `_side_effect` (goal output already unwraps), and
  sequence assembly (`default.sequence` / subsequence kernels — they should
  accept `PolyArray`/numpy without forcing sitk).
- Risk: medium. It changes *where* the sitk copy happens, not the results.
  Guard with the existing bit-identical tests.

### The GIL is NOT the wall
All kernels (numba `nogil=True`, sitk C++) already release the GIL — compute
runs truly parallel. The 200x is invisible because per-element Python/copy
overhead (~1ms) dwarfs the kernel (~0.01ms): kernel is <1% of wall time, so
its speed is unmeasurable regardless of threading. `prange`/multi-core inside
one kernel call was tried and was **slower** (0.14ms vs 0.01ms) — elementwise
ops are memory-bandwidth-bound on one image pass, not compute-bound; sitk's
200x is saved MEMORY TRAFFIC (N passes → 1), not saved FLOPs. More cores on
one image doesn't help. Free-threaded Python wouldn't help either — it would
parallelize the coordination overhead, not eliminate it. **Only fix: make the
sweep-batch the scheduling unit (Lever B below), not the element.**

### Lever B — Cross-element fusion for sweeps (the order-of-magnitude lever)
A threshold sweep runs the **same cone shape over the same image N times**
(different constant each time). Today that's N passes over the image. One
compiled kernel taking N constants and writing N outputs in **ONE pass** over
memory is where 10–20× lives for sweep workloads — the user's actual BraTS
oracle/calibration pattern (see memory `brats*`).
- This is **plan-level**, beyond cone scope (cones are per-element). It needs a
  new fusion mode that recognizes "same shape, same array inputs, varying
  scalar" across loop elements and batches them.
- Risk: high. New mechanism. Design carefully with the supervisor first.

**Why existing fusion (Stage A/B) doesn't already do this, and why "just wait
for bigger cones" doesn't work either:** cones grow by walking DEPENDENCY
EDGES from a seed (producer→consumer). Current fusion batches OPERATIONS
*within one loop iteration* (vertical: leq_sv→and→not→...→not for ONE
threshold) — that's the cone-size-9/21/61 numbers in §7 of
frontier-scheduler.md. Different loop iterations (`chain(3)` vs `chain(7)`)
are NOT connected by any edge — both depend only on `img`, not on each other
— they are parallel sibling subgraphs, not a chain. There is no edge to walk
from one iteration to another, at any cone size, no matter how long you wait
to schedule. Lever B requires a DIFFERENT mechanism: detecting that N sibling
subgraphs (the unrolled loop's per-element bodies) are *isomorphic except for
one scalar*, and synthesizing one new node that processes all N together —
not cone growth, a cross-sibling pattern match. This is also why the `for`
loop "doesn't already do this": it's deliberately generic (dynamic length,
arbitrary bodies, admission-windowed for out-of-core scale) and has no
concept of "these N bodies are the same shape" — that recognition doesn't
exist yet anywhere in the codebase.

**The GIL is not the obstacle here either** — see "GIL is NOT the wall" above.
The fix is collapsing N scheduler turns into 1, not parallelizing them.

**Payoff projection (UNVERIFIED — first thing to benchmark, not to promise):**
today cost ≈ N × (floor + kernel); batched ≈ 1 × floor + N × kernel — the
floor is paid once instead of N times. As N grows, the batched time is
increasingly kernel-dominated, and speedup should trend toward the kernel's
own ~100–300× ceiling (not the current 3–5×). For a large BraTS-style oracle
sweep (hundreds of threshold points per case, see `brats-oracle-approach`
memory) this could be the "huge speedup" the user is after — but the "~1ms
floor" number is *inferred* from the depth-vs-speedup curve, never isolated
and measured directly. First task under Lever B: measure the floor alone
(e.g. time N no-op ready-queue round trips with a trivial 1-member cone) before
committing to the batched-kernel design.

### Lever C — Reduce the per-element scheduler floor
The ~1 ms/element coordination cost. Lower-priority; it's a scheduler concern,
not a fusion one, and was already studied (diminishing returns on cheap
kernels).

### Honest ceiling
Real BraTS pipelines use `dt`, `imopen`, percentiles, connected components —
none of which fuse. Amdahl caps *whole-program* gains no matter how fast the
elementwise part gets. Fusion helps the elementwise-heavy inner sweeps, not
the morphology/distance backbone.

---

## 0b. VERIFIED on the real workload (`test_speedup.py` / TACAS'19 recipe) —
## fusion changes NOTHING here, and here's the actual bottleneck

The user ran `python3 test_speedup.py --branch feat/pointwise-fusion --cases 20`
(in `looping_experiment/`) and measured **identical wall time** to `incoming`
(no fusion at all). This is expected, not a bug — verified below, not
theorized.

### Why: this pipeline is almost entirely non-elementwise
The recipe (`test_speedup.py`'s `TEMPLATE`, same as `brats001..019.imgql`):
```
preprocess(flair) = percentiles(flair, not(touch(leq_sv(0.1,flair), border(flair))), 0)
segment(pflair)    = grow(smoothen(geq_sv(hi,pflair),5.0), smoothen(geq_sv(vi,pflair),2.0))
dice(pred,gt)       = volume(and(pred,gt)) * 2 / (volume(pred)+volume(gt))
```
`touch`, `grow`, `smoothen`, `border`, `percentiles`, `volume` are ImgQL-level
macros (`implementation/python/voxlogica/primitives/vox1/compat.imgql`) built
from `near`/`through`/`dt`/`mask` — **none of these six have `ElementwiseSpec`**
(only `not/and/or/leq_sv/geq_sv/between` do, see `vox1/__init__.py` `_ELEMENTWISE`).
`smoothen(a,x) = distleq(x,distgeq(x,!(a)))` expands through `pdt(z)=mask(dt(z),...)`
— i.e. every `not`/`geq_sv`/`leq_sv` call is sandwiched between non-elementwise
ops, so cone growth (walks edges, stops at any non-elementwise node) never gets
past 1–2 members.

**Measured directly** (synthetic case, same recipe, instrumented `FusionPlanner.plan`):
```
cone sizes seen: [2, 2, 2, 2, 2, 2, 2]
kernels_executed: 53   cones_dispatched: 7   ops_fused: 7   cones_numba: 0
```
Max cone = 2 members. `_MIN_MEMBERS_FOR_STAGE_B` is 12 — Stage B never even
had a chance. **Conclusion: don't spend a single credit trying to fuse tacas19
harder. It structurally cannot fuse beyond 2.** If elementwise fusion ever
matters for this codebase, it's for OTHER programs with long elementwise
chains (threshold sweeps, mask combination pipelines), not this one.

### Where the real time goes (profiled: one case, BraTS-real size 155×240×240)
```
percentiles            0.934s   66%
near / BinaryDilate     0.179s   13%   (inside touch/grow)
ReadImage (I/O)          0.092s    6.6%
scheduler/executor      ~0.000s   <1%   (noise — confirms fusion is irrelevant here)
```
Scheduler overhead is unmeasurable at this scale. The entire discussion of
Stage A/B/floor/GIL above is about a DIFFERENT regime (thousands of cheap
elementwise ops) — irrelevant to this workload's actual cost.

**`percentiles` decomposed** (`kernels.py:867`, numba path `_percentiles_numba`
at `kernels.py:822`, on 8.9M voxels):
```
full algorithm:        0.932s
  argsort alone:        0.86s (numba) / 0.75s (numpy)  <- 93% of percentiles' cost
  mask scan:            0.001s
  scatter-write:        0.015-0.019s
```
Not a numba bug — numpy's own argsort is just as slow (0.75-0.82s). It's the
genuine cost of an exact O(N log N) sort over the whole image, single-threaded.

**But sort parallelizes — unlike elementwise ops:**
```
4 threads, argsort on 4 independent N/4 chunks: 0.181s   (vs 0.750s single-thread)
→ ~4.1x, near-linear with core count
```
This is the OPPOSITE finding from the elementwise case: elementwise ops are
memory-bandwidth-bound on one pass (more cores didn't help, `prange` was
*slower*, see "GIL is NOT the wall" above); sorting has real computational
density and scales with cores.

### SHIPPED: parallel percentiles (this session, committed)

`percentiles()` (`kernels.py`) now: extracts the population, and if it's
≥`_PARALLEL_SORT_MIN_POPULATION` (200,000) argsorts `_PARALLEL_SORT_CHUNKS`
(≤8) contiguous chunks in PARALLEL THREADS (`np.argsort` releases the GIL —
real parallelism), then merges the sorted chunks back with a BALANCED
binary-tree of two-pointer merges (`_merge_sorted_pairs`, numba `@njit`,
O(n1+n2) each — see the pitfall below). Below threshold: unchanged
single-thread `np.argsort`, byte-for-byte the old behavior.

**A real pitfall hit and fixed while building this**: the first merge
implementation used a vectorized `np.searchsorted`-based trick (no
Python-level loop) instead of a loop — clever, but for two COMPARABLY-SIZED
arrays it's O(n log m), the SAME asymptotic order as sorting. Profiling after
"success" showed the merge step now dominating (0.75s in `searchsorted`
calls) — the sort got faster but the merge silently absorbed the win. Fixed
by writing a genuine O(n) two-pointer merge in numba, and switching from a
LINEAR fold over the K chunks (O(N·K) total merge work) to a balanced binary
tree (O(N·log₂K)). **Lesson: profile again after "fixing" something —
vectorized-but-still-superlinear is an easy trap.**

**Correctness**: tie order across the merged inputs doesn't matter — the
grouping step assigns the same output value to an entire run of equal
values regardless of which physical index appears first within it — so the
merge doesn't need to replicate `np.argsort`'s exact tie-breaking, only
produce a validly sorted sequence. Proven and tested
(`tests/unit/test_percentiles_parallel_sort.py`, 9 cases: tie-heavy
populations, empty/all-masked-off, exact-threshold boundary, odd chunk
remainders, bit-identical against an independent plain-numpy reference).

**Measured** (BraTS-real single case, 155×240×240, `flair`+`grow`+`dice`
pipeline, warm imports):
```
percentiles alone:  0.934s -> 0.213s   (4.4x)
whole-case wall:     1.4s  -> ~0.66s   (2.1x)  — WITHOUT touching fusion/scheduler
```
Confirms the Amdahl projection almost exactly. `near`/BinaryDilate (0.177s)
and ReadImage (0.088s) are now comparable in size to `percentiles` (0.213s)
— the profile is far more balanced; there is no longer one dominant single
target. Next targets if more speed is wanted: `near`/dilation, and/or
parallelizing the merge-tree's rounds themselves (currently sequential
across pairs within a round — the pairs ARE independent and could run in
their own threads too, untried).

### Action item for the new pair
1. Do NOT continue Lever A/B/C work assuming it will speed up TACAS'19 —
   it structurally can't (max cone size 2). Fusion work only matters for
   different (elementwise-heavy) programs.
2. Parallel `percentiles` is DONE and committed (see "SHIPPED" above) — a
   2.1x whole-case win, verified. Don't re-do it; do re-verify it on the
   real dataset (see the looping-experiment prompt below, item 3) since all
   the numbers above are from ONE synthetic case.
3. If more speed is wanted next: `near`/dilation (13% of one case) is the
   next largest single target; parallelizing the merge-tree's rounds
   (independent pairs, currently sequential) is a smaller, easy follow-up
   to the percentiles work already in place.

## 0c. How the profiling above was done — and the new dedicated switch

There was NO existing way to profile a real `.imgql` program through the
actual CLI — `bench_scheduler.py --profile` only profiles its own synthetic
benchmark programs. Everything in §0b was done ad hoc: a standalone script
importing the engine directly, building/parsing the program, then:
```python
import cProfile, pstats
prof = cProfile.Profile()
prof.enable()
asyncio.run(engine.run())   # or the full _drive() coroutine
prof.disable()
pstats.Stats(prof).sort_stats("cumulative").print_stats(30)   # or "tottime"
```
That's it — cProfile around the `asyncio.run(...)` call that drives the
engine. `tottime`-sorted output finds the actual hot function; `cumulative`
finds the hot call path. (For microbenchmarking one suspect function in
isolation — like the `argsort` breakdown above — just time it directly with
`time.perf_counter()` before/after, no profiler needed.)

**This session added a real `--profile` switch to the CLI** (see the diff —
`voxlogica run --profile [PATH]`, wraps `EngineExecutionStrategy.run()`'s
`asyncio.run(evaluate())` in `cProfile`; no path = prints top-30 by
cumulative + top-30 by tottime to stderr; a path dumps raw `.pstats`, load
with `pstats.Stats(path)` or `snakeviz path`). Use it instead of ad hoc
scripts going forward:
```
python -m voxlogica.main run my.imgql --profile                          # top-30 to stderr
python -m voxlogica.main run my.imgql --profile=/tmp/out.pstats          # then: snakeviz /tmp/out.pstats
```
NOTE the flag comes AFTER the filename — argparse's `nargs='?'` greedily
eats the next token as `--profile`'s value if given before the positional
`filename`, breaking the parse (verified: `run --profile file.imgql` fails
with "filename required"; `run file.imgql --profile` works).
`--profile` has no effect with `--no-engine` (lazy strategy) — a warning is
logged, not a silent no-op.

---

## 1. The two-role protocol

### SUPERVISOR (GPT) — was Fable's role
Owns: design, task decomposition, code review, correctness reasoning, deciding
what to measure. Does NOT write large code diffs. For each unit of work:
1. Write a precise, self-contained spec/prompt into a scratch file or inline
   (what to change, which files, invariants to preserve, how to verify).
2. Hand it to the executor.
3. Review the executor's diff against the spec and the codebase conventions.
4. Insist on measurement before claiming a speedup (bench numbers, not theory).

### EXECUTOR (Cursor) — was Sonnet's role
Owns: implementation, running tests/benchmarks, reporting results. For each unit:
1. Implement exactly the spec. Ask (pause and surface the question) if a
   moderately-risky assumption comes up — don't guess on irreversible or
   architectural choices.
2. Run the relevant test subset + a benchmark. Report actual numbers.
3. Keep diffs minimal and match surrounding code style (module docstrings
   stating invariants, O(frontier) discipline, no dead code, no speculative
   generality).

### Working rules carried over (from `.claude/CLAUDE.md`)
- **Quiet mode**: no narration of routine steps; 1–2 line summary at the end.
  Speak up only for (a) high-risk/irreversible file or data decisions —
  ask first, and (b) moderately-risky assumptions — state them briefly.
- **ADHD mode**: answer/conclusion first; short, skimmable, bulleted; one
  question at a time; track goal / current state / blocker / next step.
- **Parallel model**: run isolated experiments in parallel, collect together;
  never sequentially when they're independent.
- Never paste raw shell output into the chat/context — redirect to a file,
  check its size, read only the relevant excerpt.
- Commit/push only when the user explicitly asks.

---

## 2. Current state (what's done and correct)

Phases 0, 1, 2 of the fusion plan are shipped (see
`doc/specs/semantic-queueing-fusion.md` §8):
- **Stage A** — batched cone dispatch (real kernels, one pool task per cone).
- **Stage B** — numba-compiled flat per-voxel loops, background compile,
  shape-keyed cache, minimum-cone-size gate.

Key design facts the next pair must not re-derive:
- **Stage B decision runs on the pool thread** (`Executor.run_cone_auto`), never
  the event loop — because `shape_of` can force a `PolyArray`'s first numpy
  view, which calls sitk C++ image code that must not race another thread.
  `PolyArray` view construction is lock-guarded (`arrays.py`).
- **numba `cache=True` is impossible here** — it re-imports the function's
  defining module on a cache hit, but generated kernels live in an `exec()`'d
  namespace with no real module. We use `cache=False` + an in-process
  shape→callable dict. No cross-process cache, and no cross-*query* cache
  (each `EngineExecutionStrategy.run()` makes a fresh engine + fresh backend).
  The target win is *within one query*: a loop's thousands of identical-shape
  per-element cones warm exactly one compile.
- **Minimum cone size = 12** (`_MIN_MEMBERS_FOR_STAGE_B`, env
  `VOXLOGICA_NUMBA_MIN_MEMBERS`). Below it Stage B is a *measured net loss*
  (the exit copy, leak #1, isn't amortized). This gate is why short/simple
  programs see zero regression.
- **Output selection is already "needed"** — Stage B writes only cone exits;
  the `_rematerialize` recompute fallback was already generic (any missing
  value is recomputed regardless of why). No `FUSION_OUTPUTS=all` toggle was
  needed.
- **Vector images refused** by `shape_of` — real sitk comparison kernels fail
  on them; a flat loop would silently "succeed" per-component. Bit-identical
  means identical failures too.

### Kill switches / knobs (env)
- `VOXLOGICA_FUSION=0` — disable all fusion (planner returns None).
- `VOXLOGICA_NUMBA_FUSION=0` — disable Stage B only (keep Stage A).
- `VOXLOGICA_NUMBA_MIN_MEMBERS=N` — Stage-B minimum cone size (default 12).
- `VOXLOGICA_FUSION_CAP=N` — max cone size (default 64).

### Metrics (from `engine.metrics()`)
`cones_dispatched`, `cones_numba`, `ops_fused`, `mean_cone_size`,
`interiors_elided`, `numba_compiles_started/finished/failed`.

---

## 3. File map (where to work)

```
implementation/python/voxlogica/
  arrays.py                    PolyArray: one value, many lazy cached views (sitk/numpy).
                               Lock-guarded view build. THE place lever A touches.
  engine/
    numba_fusion.py            Stage B: ConeShape, shape_of, codegen, compile, backend.
    executor.py                run_cone / run_cone_auto / _compute_cone_numba; _unwrap (leak #1).
    fusion.py                  FusionPlanner (Stage A cone growth). Cross-element = lever B.
    core.py                    ComputationEngine._worker: cone dispatch site + bookkeeping.
    config.py                  EngineConfig: all the knobs above.
    strategy.py                EngineExecutionStrategy: per-query engine (why no cross-query cache).
  primitives/vox1/__init__.py  _ELEMENTWISE: the 6 expr fragments (not/and/or/leq_sv/geq_sv/between).
  primitives/vox1/kernels.py   the real sitk kernels those fragments must match bit-for-bit.

tests/unit/
  test_numba_fusion.py               Stage B: bit-identical A vs B, NaN/boundary, elided-interior remat.
  test_fusion_engine_integration.py  Stage A end-to-end + the stage-pin deadlock regression.
  test_fusion_planner.py             cone growth.

tests/perf/
  bench_numba_fusion.py        Stage A vs A+B throughput (author added this session).
  bench_scheduler.py           general scheduler throughput/memory bench.

doc/
  specs/semantic-queueing-fusion.md               the plan (phases, knobs, §3.2 residual hole).
  dev/dynamic-scheduler/frontier-scheduler.md     measured results + all the honesty notes.
```

---

## 4. How to run things (credit-cheap)

Env: `source .venv/bin/activate` from repo root; run pytest from
`implementation/python`. Python is 3.14, numba 0.64, SimpleITK 2.5.

Fusion test subset (fast, ~2s):
```
cd implementation/python && source ../../.venv/bin/activate
python -m pytest ../../tests/unit/test_numba_fusion.py \
  ../../tests/unit/test_fusion_engine_integration.py \
  ../../tests/unit/test_fusion_planner.py -q
```

Full unit suite baseline (note: 6 failures + 8 collection errors are
**pre-existing and unrelated** — missing modules `inspectable_sequence`,
`mcp_server`, and dask/range primitive tests; do not chase them):
```
python -m pytest ../../tests/unit -q --continue-on-collection-errors --maxfail=0
# expect: 129 passed, 6 failed, 8 errors  (129 = baseline 125 + our 4 new tests)
```

Benchmark (the numbers that matter for the levers):
```
python ../../tests/perf/bench_numba_fusion.py --size 128 --iters 400
```

Writing `.mha` (not `.nii.gz`) for test images with NaN/inf: NIfTI silently
zeroes them on write. Use `.mha`.

---

## 5. First tasks for the new pair

1. **Executor**: commit the uncommitted revision batch (below), then confirm
   the fusion subset is green.
2. **Supervisor**: read `frontier-scheduler.md` §"Phase 2 leg 2" and this file
   §0, then write the spec for **Lever A (lazy `_unwrap`)** — it's the cheapest
   path to a real jump because it attacks the dominant leak. Decompose it:
   (a) make sequence assembly accept numpy/PolyArray without forcing sitk,
   (b) make `_unwrap` demand-driven, (c) verify bit-identical + re-bench.
3. Only after Lever A is measured, decide whether Lever B (cross-element sweep
   fusion) is worth its complexity for the target BraTS workloads.

### Commit message for the pending batch
```
engine: Phase 2 leg 2 polish — size-gate before shape_of, vector guard, tests, bench

- Move the minimum-cone-size check ahead of shape_of so small cones (the common
  case) skip the shape walk entirely, not just fail try_get afterward.
- shape_of refuses vector images: real sitk comparison kernels reject them, so a
  flat per-voxel loop must not silently succeed per-component (bit-identical
  means identical failures).
- Tests: NaN/boundary bit-identical A-vs-B; elided-interior rematerialization
  under late hash-consing; Stage-B-disabled no-op.
- tests/perf/bench_numba_fusion.py; measured results + Amdahl analysis of why
  numba's ~200x kernel win yields 3-5x end-to-end (numpy->sitk exit copy is the
  dominant leak) written into frontier-scheduler.md.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```
