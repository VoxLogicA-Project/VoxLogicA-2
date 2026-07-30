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
