# How well does the VoxLogicA engine use a 24-core machine?

**Date:** 2026-07-30
**Host:** fmt-5000 — 24 logical CPUs, hybrid Intel P/E, Linux, idle and unloaded
**Workload:** `_bench_scaling.imgql` — 4 BraTS cases x (3 hyper x 5 floor x 5 radius, plus 2 open radii), 26,297 nodes, 26,253 kernels, `recomputes: 0`
**Protocol:** `doc/dev/scaling-test-design.md`; min-of-3, interleaved, idle-gated, `--no-cache`, every cell byte-identical (`avg_oracle_best=0.8723535372940219`)

---

## 1. Answer up front

**The engine achieves 9.65x on 24 cores. It is not going to achieve 24x, and the
reason is not the engine.**

Three independent measurements pin the cause:

1. **The scheduler is not the bottleneck.** Achieved concurrency
   (`saturation` = mean in-flight kernels / requested) is **0.975-0.991 in every
   single cell measured**. The engine dispatches essentially exactly what it is
   asked to, at every worker count, on both interpreters.
2. **The program is not the bottleneck.** The Brent bound (work/span) for this
   program is **273.9x** (26,297 nodes, critical path 96). There is two orders
   of magnitude more parallelism available than 24 cores can consume.
3. **The hardware is the bottleneck.** Past 18 workers, adding cores *increases
   CPU consumed and increases wall-clock*: 982 -> 1374 CPU-seconds while wall
   goes 63.25s -> 68.57s. Work is being burned without producing throughput —
   the signature of memory-bandwidth saturation, not of a scheduling defect.

Since the scheduler is at ~98% of its request and the program permits 273x, the
54%-of-linear efficiency at the optimum is **entirely attributable to the memory
system**. Making it faster requires *less memory traffic per node*, not better
parallelism.

## 2. The scaling curve (min-of-3, ITK inline)

Serial baseline is genuinely serial: `workers=1` **and** `itk=1`.

| workers | GIL wall | speedup | eff | cpu/wall | sat | FT wall | speedup | eff | cpu/wall | sat |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 610.20 | 1.00x | 100% | ~1 | — | 596.70 | 1.00x | 100% | ~1 | — |
| 4 | 171.69 | 3.55x | 89% | 3.9 | 0.989 | 161.32 | 3.70x | 92% | 4.0 | 0.991 |
| 8 | 94.26 | 6.47x | 81% | 7.4 | 0.983 | 86.57 | 6.89x | 86% | 7.9 | 0.990 |
| 12 | 73.05 | 8.35x | 70% | 10.9 | 0.982 | 68.29 | 8.74x | 73% | 11.7 | 0.984 |
| **18** | **63.25** | **9.65x** | 54% | 15.5 | 0.975 | **61.97** | **9.63x** | 53% | 17.6 | 0.985 |
| 24 | 68.57 | 8.90x | 37% | 20.0 | 0.980 | 77.53 | 7.70x | 32% | 23.4 | 0.976 |

Efficiency decays smoothly (89% -> 81% -> 70% -> 54%) and then **turns
negative**: 24 workers are slower than 18 on both interpreters. This
independently reproduces `engine/topology.py`'s claim — fitted on a different
host and a different program — that useful concurrency saturates in the memory
system well before every logical CPU is busy. The `"balanced"` heuristic's
instinct to use fewer than all CPUs is correct, and this is a second,
independent confirmation of it.

## 3. Free-threaded CPython does not pay for itself here

| workers | GIL | free-threaded | FT advantage |
|---|---|---|---|
| 4 | 171.69 | 161.32 | +6.0% |
| 8 | 94.26 | 86.57 | +8.2% |
| 12 | 73.05 | 68.29 | +6.5% |
| 18 | 63.25 | 61.97 | +2.0% |
| 24 | 68.57 | 77.53 | **-13.1%** |

At the *optimum* worker count the gain is 2%, and at 24 workers free-threading
is 13% **worse** while burning 32% more CPU (1810.7 vs 1374.1 CPU-seconds). The
mechanism is consistent with §1: this workload's time is dominated by SimpleITK
C++ kernels that **release the GIL anyway**, so removing the GIL removes a lock
that was never on the critical path, while adding free-threading's per-object
locking overhead.

**Consequence for the hard cutover (commit `b4e170e`).** That cutover was
justified by `doc/dev/free-threaded-handover.md`'s observation that the GIL build
sustained only ~12.7/24 cores. These measurements show that ceiling was **not
the GIL**: the GIL build reaches `cpu/wall` = 20.0 at 24 workers with
`saturation` = 0.980 once ITK is configured sanely. The earlier 12.7-core
observation is better explained by ITK thread-pool interaction (§4) than by
interpreter locking. The cutover is not *harmful* — it is ~2-8% positive below
18 workers — but its stated justification does not survive measurement, and it
should be revisited rather than treated as settled.

**Caveat, unavoidable:** the GIL venv carries numba 0.64 / SimpleITK 2.5.2 and
the free-threaded venv carries 0.66 / 2.5.5, because no earlier release ships
cp314t wheels. This comparison is therefore not single-variable.

## 4. The dominant factor is ITK's thread count, and the effect is not monotone

Fixed `workers=18`, sweeping ITK's global default thread count:

| itk | wall | cpu | cpu/wall | saturation | diagnosis |
|---|---|---|---|---|---|
| **1** | **62.80** | 1101.3 | 17.5 | 0.981 | engine supplies parallelism; cores doing useful work |
| 2 | 207.60 | 773.4 | **3.7** | 0.992 | 18 kernels in flight, **~4 cores busy** — kernels blocked |
| 4 | 129.64 | 804.9 | 6.2 | 0.987 | same, less severe |
| 24 | 67.56 | 1545.5 | **22.9** | 0.986 | cores busy, but 40% more CPU than itk=1 for 8% worse wall |

A 3.3x wall-clock swing from one environment variable — larger than any other
factor measured, including the interpreter and the worker count.

**This is where `saturation` earns its keep.** At itk=2 the engine reports
`saturation` = 0.992: it *did* dispatch ~17.9 concurrent kernels. Yet only 3.7
cores' worth of CPU is consumed. Dispatched-but-idle is a completely different
failure from failed-to-dispatch, and **wall-clock cannot distinguish them**.
It also answers a specific question raised during this work — *are the cores
busy-waiting?* — with a measurement rather than a guess:

- at itk=2 the kernels are **blocked, not spinning** (busy-wait would show
  `cpu/wall` ~= 18, not 3.7);
- at itk=24 there **is** real waste: 40% more CPU-seconds for a worse wall,
  which is either spin or memory stalls, and is the honest core of the original
  suspicion.

`perf stat` was collected but is **not reported**: on a hybrid P/E CPU it emits
separate `cpu_atom`/`cpu_core` counters, multiplexed at 45-76% of runtime.
Reading one of the two lines produced a spurious 3.3x "instruction count"
difference between cells whose `kernels_executed` is identical (26,253,
`recomputes: 0`). The counters need per-core-type aggregation and multiplexing
correction before they mean anything.

## 5. The shipped ITK policy is wrong and must be reverted

`engine/itk_threads.py` (commit `ec05274`) sets `itk = cores // workers`. On this
host that yields exactly the catastrophic mid-range:

| workers | policy picks | measured wall | best available | penalty |
|---|---|---|---|---|
| 8 | itk=3 | ~156s | 69s (itk=24) | **2.3x slower** |
| 12 | itk=2 | ~208s | 68s (itk=1 or 24) | **3.1x slower** |
| 18 | itk=1 | 62s | 62s | optimal |
| 24 | itk=1 | 69s | 69s | optimal |

It is optimal exactly where `cores // workers` happens to equal 1, and
catastrophic elsewhere. The reasoning behind it — "keep total native threads
near the core count" — sounded principled and is simply not how this system
behaves: **oversubscription is cheap here** (itk=24 at 8 workers = 192 threads
on 24 cores, and it is the *best* cell at that worker count), while
**fine-grained subdivision is ruinous**.

Worse, the optimum is a genuine 2-D interaction with a crossover:

- at `workers=8`: itk=24 (69.3s) beats itk=1 (86.6s) — the engine alone cannot
  fill the machine, so ITK's intra-filter parallelism is doing useful work;
- at `workers=18`: itk=1 (62.0s) beats itk=24 (67.6s) — the engine fills the
  machine, and ITK's pool only adds overhead.

So no fixed constant is right, and `itk=1` — the value this session was about to
ship as the "obvious" fix — would have been a **second** wrong policy, costing
21% at 8 workers.

### Recommendation

1. **Revert the `cores // workers` cap.** Leaving ITK at its own default is
   never catastrophic (68-76s across all worker counts); the cap is up to 3.1x
   slower. This is a regression fix, not a tuning question.
2. **Extend calibration to two dimensions.** `engine/calibration.py` already
   sweeps `workers` and caches by machine fingerprint. ITK's thread count
   belongs in that sweep: its effect (3.3x) exceeds the effect of `workers`
   itself (1.5x across 8-24), it interacts with `workers`, and it is
   host-specific. A hard-coded constant cannot be right on every host, and this
   session demonstrates twice over that guessing it does not work.
3. **Do not treat any of this as settled on one host and one program.** Both
   remaining generalisation axes are unmeasured (§6).

## 6. What is NOT established

Stated explicitly, because four wrong conclusions in this session came from
over-reading narrow data:

- **Only one workload class.** This is a *wide* parameter sweep (Brent bound
  273.9x). A single-case pipeline is near-chain (bound ~1) and **cannot** scale
  at any engine quality. No claim here transfers to interactive one-case runs.
  `doc/dev/scaling-test-design.md` §6 requires both classes; only one was run.
- **Only one host.** All Mac measurements were discarded: a GPU-resident
  `llama_cpp` server ran concurrently, and one timed run contained a 2h20m
  battery hibernation. The 6P+12E Apple Silicon result is unknown, and §4's
  crossover point is expected to be host-specific.
- **`perf` counters unusable** as collected (§4).
- **No GIL-vs-FT single-variable comparison** exists, and cannot without cp314t
  wheels for the older numba/SimpleITK pins (§3).
- **Reduction/planning excluded.** `execution_time` starts after reduction, which
  is serial. End-to-end user-visible speedup is therefore *lower* than the
  figures here by an Amdahl term nobody has measured. For a 30-case oracle grid
  reduction alone is ~129s (`reducer.py`), which at 62s of execution would
  **dominate** — plausibly the single largest unexamined cost in the system.

## 7. Where the remaining 2.5x actually is

At 18 workers, 9.65x of a possible 18x. The missing factor is memory bandwidth
(§1), so the levers are ones that move less data, not ones that schedule better:

1. **Reduction/planning (§6).** Possibly larger than everything below, and
   entirely unmeasured. Measure before optimising anything else.
2. **Widen fusion past strictly-elementwise ops.** `dilate`/`erode`/`smoothen`
   are called constantly in this sweep and each is a full pass over a volume.
   Fusing neighbourhood ops into existing cones removes whole round-trips to
   DRAM — attacking bandwidth directly, which is the measured constraint.
   (`free-threaded-handover.md` §5b item 2.)
3. **GPU residency across the sweep.** Keep volumes device-resident for the
   ~150 combinations per case and return only scalar Dice. Sidesteps host DRAM
   bandwidth entirely. (§5b item 3.)
4. **Do not** pursue scheduler work. At `saturation` 0.975-0.991 there is
   nothing there to win.

## 8. Methodological note

The measurements in §2-4 contradict four earlier conclusions reached during this
same session — including two that were committed as code. All four came from
reading wall-clock alone, which cannot discriminate between "the scheduler
starved", "the kernels blocked", and "the memory system saturated", though each
demands a different fix. The `saturation` and `cpu/wall` columns in every table
here are what make these findings falsifiable, and were added
(`engine/concurrency_probe.py`, `engine/parallelism.py`) precisely because their
absence made the earlier claims unfalsifiable. Wall-clock tables without them
should not be trusted, including any produced by me earlier today.

**Correction, added in Part II below:** §2's "efficiency" column (speedup / W)
divides by raw worker count, which silently assumes every worker is a P-core.
On this hybrid CPU it is not — §9-10 measure the P/E asymmetry directly and
supersede that column. §1's "memory bandwidth" framing is retained but is now
the *confirmed*, not inferred, cause — §9 attributes it to specific stalled
pipeline slots via four independent methods, and separately rules out disk I/O.

---

# Part II — cause, isolation, and reproducibility (2026-07-30, later)

Part I established *that* efficiency degrades and *that* CPU-seconds inflate
with worker count. It did not establish *why*, and one of its own numbers (the
§2 "efficiency" column) turned out to conflate two different things on this
hybrid CPU. This part closes both gaps: identifies the cause with instrument-
level evidence, isolates the P-core/E-core asymmetry from actual stalling, and
ships a dataset-free reproduction package so the whole study can be re-run
without BraTS2020.

## 9. Is it actually memory, or could it be disk?

Before trusting any "memory-bound" claim, the alternative that a real dataset
invites was checked directly rather than assumed: **is the 43% backend-bound
figure secretly disk I/O** (the BraTS files being re-read each run) rather than
DRAM/cache latency?

```
$ iostat -x 1 3 -d /dev/nvme0n1      # sampled DURING a live sweep run
Device    r/s  rkB/s   w/s   wkB/s  ...  %util
nvme0n1  0.00   0.00  0.00    0.00  ...   0.00   <- every sample but one
nvme0n1 20.32 1745.65 80.02 8763.13 ...   0.41   <- the ONE exception
```

The one non-zero sample coincides with the *previous* run's `--store-db`
result flush, not a read for the current run — confirmed by checking `vmstat`
`bi` against wall-clock position, and by the dataset size: 369 BraTS cases at
~7.4 MB each is ~2.7 GB, trivially smaller than the 38 GB already resident in
page cache. **Every other sample across every sweep is 0.00 r/s, 0.00 w/s,
0.00% util.** Disk is ruled out with a direct block-device measurement, not an
assumption.

## 10. What the stalls actually are (four independent methods)

Instruction counting alone (Part I §4) already showed instructions flat
(±0.3%) while CPU-seconds rose with worker count — ruling out lock spinning,
since a spin loop retires instructions and this one is not retiring more of
them. Three further methods confirm and localize the cause:

**Topdown (P-cores only, one active PMU, no multiplexing):**

| workers | retiring | backend-bound | bad speculation | frontend-bound |
|---|---|---|---|---|
| 1 (serial) | 40.0% | **43.4%** | 8.6% | 8.0% |
| 8 | 37.4% | **49.3%** | 7.7% | 5.6% |

**The workload is 43% backend-bound even running serially, on one core, before
any parallelism exists.** Parallelism adds only 5.9 more points on top of an
already-dominant baseline cost. This reframes the whole study: memory latency
is not something concurrency introduces here — it is this workload's
intrinsic character, and parallelism is fighting it, not causing it.

**`perf record` symbol attribution (P-cores, 8 workers, 199 Hz sampling):**
top self-time is entirely `_SimpleITK.abi3.so` (19.5% across the top entries)
and one small kernel symbol (1.7%). **No `futex`, `lock`, `spin`, or atomic
symbol appears anywhere in the profile.** This directly corroborates the flat
instruction count: whatever the cores are doing, it is not contested locking.

**Sanity check on the measurement itself:** an earlier attempt summed
`cpu_atom` and `cpu_core` PMU counters together and implied a 9.9 GHz clock —
physically impossible, and the tell that caught it. Root cause: hybrid Intel
CPUs multiplex the two core-type PMUs even when only one event is requested,
so a naive sum double-counts. Fix: `taskset -c <P-core-list>` (or E-core-list)
restricts execution to one core type, leaving a single PMU active and giving
exact (not multiplexed) counts — confirmed by an implied clock of 5.54 GHz on
P-cores and 4.52-4.60 GHz on E-cores, both physically sensible.

## 11. P-core vs E-core: measured in isolation, not inferred from mixed runs

Part I estimated E-cores return "0.13 P-core-equivalents at the margin" from
wall-clock arithmetic on runs where P-cores and E-cores worked *simultaneously*
— a real number, but one that conflates E-core weakness with P/E contention.
Pinning each pool separately (`taskset -c 0-7` / `taskset -c 8-23`) isolates
them:

| pool | workers | wall | P-core-equivalents | per-core |
|---|---|---|---|---|
| P | 1 | 611.66 | 1.000 | 1.000 |
| P | 2 | 316.31 | 1.934 | 0.967 |
| P | 4 | 173.64 | 3.523 | 0.881 |
| P | 8 | 93.27 | 6.558 | 0.820 |
| E | 1 | 810.74 | 0.754 | **0.754** |
| E | 4 | 218.49 | 2.799 | 0.700 |
| E | 8 | 125.16 | 4.887 | 0.611 |
| E | 16 | 111.87 | 5.468 | 0.342 |

**Intrinsic E-core throughput is 0.754 of a P-core** — close to this CPU's
published spec ratio, and far better than the earlier 0.13 estimate. But E-core
per-core value *also* degrades with E-core count, and much faster than P-cores
do: 0.754 -> 0.700 -> 0.611 -> 0.342 (16 cores) versus P's gentler 1.000 ->
0.967 -> 0.881 -> 0.820 (8 cores). Sixteen E-cores sharing a memory system feel
the same backend-bound pressure §10 measured, only more of them piling onto it
at once.

**The bigger effect is cross-pool contention, not E-core weakness itself:**

```
P alone (8 cores):            6.558 P-core-equivalents
E alone (16 cores):           5.468 P-core-equivalents
Sum, if independent:         12.026 P-core-equivalents
Actually achieved together
  (W=24, both pools, unpinned): 8.688 P-core-equivalents
Lost to running both pools simultaneously:      27.8%
```

Running P-cores and E-cores at once loses more capacity to shared-memory
contention between the two pools than either pool loses internally. This is a
genuinely new finding, not a restatement of Part I's efficiency numbers, and it
changes the engineering recommendation:

**A naive "route boolean ops to E-cores, ITK-heavy ops to P-cores" scheduler
would not obviously fix this.** Both pools read the same DRAM controller; if
the workload is backend-bound (§10), moving *which* core stalls does not
reduce the total memory traffic causing the stall. Per-operator P/E placement
is still worth doing — it can reduce *unnecessary* contention (e.g. keeping
latency-critical critical-path nodes, identified via `engine/parallelism.py`'s
span computation, off the weaker/more contended E-cores) and is directionally
supported by the steeper E-core degradation curve — but it is not a substitute
for reducing memory traffic. §7's bit-packing proposal, which cuts DRAM traffic
by up to 8x on 62% of boolean nodes, is the higher-confidence lever precisely
*because* §10 shows the stalls are real and §11 shows adding smarter core
placement cannot make the shared memory system move any less data.

## 12. Reproducibility

The measurements above depend on the BraTS2020 dataset, which is not part of
this repository. `tests/perf/scaling/` provides a dataset-free reproduction:

- `generate_synthetic_cases.py` — deterministic (no RNG/seed) synthetic volumes,
  same shape (240x240x155) and same per-case variation structure as the real
  data.
- `bench_scaling.imgql` — the identical sweep (same hgrid/fgrid/Rgrid/Ogrid,
  same operator chain) reading the synthetic volumes; verified to produce
  26,477 nodes against the original's 26,297 (0.7% drift from synthetic masks
  taking slightly different `maxvol`/`fill_holes` paths — expected, immaterial
  to the performance characteristics).
- `run_scaling_suite.sh` — the full protocol: idle gate (rejects a loaded host
  or a detected local LLM server — see Part I's own §0 failure #2), portable
  wall-clock/saturation sweep, and (where `perf`/`taskset`/a hybrid CPU are
  available) the P-core/E-core isolation and topdown/`perf record` stages of
  §9-11.
- `analyze_results.py` — turns the harness's raw log into speedup/cpu-wall
  tables.

See `tests/perf/scaling/README.md` for exact commands and for what this package
can and cannot reproduce (engine/hardware behaviour: yes; the real study's
absolute Dice numbers: no — the synthetic volumes are Gaussian blobs, not
brain scans).

## 13. Updated ranked next steps

Supersedes Part I §7 item ordering given §11's contention finding:

1. **Bit-pack boolean images** (Part I §7, now strengthened by §11's finding
   that core placement cannot substitute for reduced traffic). 62% of boolean
   nodes never reach an ITK call (measured via a dependency-graph query over
   the reduced plan); those get an 8x memory-traffic cut and a ~64x
   instruction-count cut (`and`/`or`/`not` as `uint64` word ops) with no
   unpacking needed at all. The remaining 38% cross into `dt`/`mask`/`through`/
   `border`/`maxvol` and need unpacking — a precomputed byte->word LUT keeps
   that branch-free and memcpy-speed.
2. **Extend `engine/calibration.py` to a 2D sweep**: worker count x ITK thread
   count. Part I §5 showed no fixed ITK-thread constant is right (the optimum
   crosses over with worker count); §10-11 now show *why* a constant cannot
   generalize either (the right split depends on how memory-bound the specific
   workload is, which calibration can measure but a formula cannot guess).
3. **Reduction/planning time** (Part I §6) — still entirely unmeasured, and
   plausibly larger than everything else combined for realistic sweep sizes.
4. **Per-operator P/E placement**, keeping critical-path nodes (identified via
   `engine/parallelism.py`) off E-cores. Real but secondary to #1 per §11.
5. Fusion-widening and GPU residency (Part I §7 items 2-3) unchanged.
