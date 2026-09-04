# Scaling measurement: test design

Status: **design + instrumentation spec.** Written 2026-07-30 after a session
that produced three mutually contradictory conclusions about the same engine on
the same day. This document exists to make that impossible to repeat.

## 0. Why the previous attempts failed

Worth recording precisely, because every failure was a *design* failure rather
than bad luck, and each one is cheap to prevent:

| # | What happened | Root cause |
|---|---|---|
| 1 | "Engine gets only 3.4x on 18 cores" | Baseline was not serial. ITK's internal pool made the `--threads 1` arm consume ~3.6 cores, deflating every ratio computed against it. |
| 2 | "Free-threading is a 4.3x regression" | One cell, one rep, on a laptop, concurrent with a GPU-resident LLM server, across a battery-hibernation event. |
| 3 | "ITK cap is a prerequisite" -> shipped `cores // workers` | Inferred a mechanism from a single anomalous cell and committed it. The clean sweep later showed those exact values (itk=2,3,4) are the *worst* region of the curve. |
| 4 | "ITK's global pool serializes filters" | Plausible story fitted to 4 points; the full itk sweep showed a smooth monotone curve with no "broken middle", refuting it. |

The common thread: **wall-clock alone cannot distinguish the hypotheses.** Every
one of these stories predicts the same wall-clock in at least one cell. Telling
them apart requires measuring *where the cores went*, not just how long the run
took. That is the central requirement below.

A second, subtler failure: **we never established what speedup was even
attainable** for the program being measured. Chasing "24x on 24 cores" is
meaningless without knowing the program's own parallelism ceiling (sec 3).

## 1. Required per-run metrics

A run that reports only wall-clock is not admissible evidence.

| Metric | Why it is necessary | Source |
|---|---|---|
| `wall` | the headline | engine `execution_time` (note: **excludes** reduction/planning, which is serial -- see sec 5) |
| `cpu_user`, `cpu_sys` | achieved parallelism = cpu/wall. Separates "cores idle" from "cores busy". | `/usr/bin/time -p` |
| `mean_concurrency` | time-integrated in-flight kernels. Distinguishes "engine did not dispatch W" from "engine dispatched W but each was slow". | **new instrumentation**, sec 2 |
| `cycles`, `instructions`, IPC | separates real work from spin/sync waste. Same program+output => instruction count should be ~invariant; a config burning more instructions for identical output is wasting them. | `perf stat` |
| `bitexact` | a faster wrong answer is not a result | compare the printed oracle value |
| `peak_rss` | rules out swap as a hidden variable | engine memory log |

`cpu/wall` and `mean_concurrency` together are the discriminator the earlier
attempts lacked:

- cpu/wall low **and** mean_concurrency low -> engine is not dispatching (scheduler/dependency limited)
- cpu/wall low **and** mean_concurrency high -> kernels are blocked (lock/IO/memory stall)
- cpu/wall high **and** wall not improving -> spin or bandwidth saturation; `perf` IPC separates those

## 2. New instrumentation: achieved concurrency

`engine/core.py` already tracks `self._in_flight` but only exposes it in the
deadlock watchdog. It is absent from `metrics()`, so no run has ever reported
whether it actually achieved its nominal `max_concurrency`. That is the single
most useful missing number.

Spec:

- a sampler thread reads `_in_flight` every 50 ms (a plain int read; no lock,
  and a torn read is impossible for a small int on CPython)
- accumulate `sum(in_flight)` and `n_samples`
- expose in `metrics()`:
  - `mean_concurrency = sum/n` -- achieved, to compare against `max_concurrency`
  - `peak_concurrency`
  - `saturation = mean_concurrency / max_concurrency`
- sampling must be cheap enough to leave wall-clock unchanged; verify by A/B
  with the sampler disabled (accept if within run-to-run noise, ~2%)

This is a permanent engine capability, not test scaffolding: `saturation` well
below 1.0 is exactly the diagnostic a user needs when a run is slower than
expected.

## 3. The parallelism ceiling (work/span)

**No engine can beat the program's own critical path.** By Brent's bound,
speedup <= work/span, where work = total node cost and span = longest
dependency chain. This must be computed *before* interpreting any speedup
number, otherwise "poor scaling" and "an inherently serial program" are
indistinguishable.

Spec for a `--report-parallelism` analysis (offline, on the reduced plan):

- work = sum over nodes of measured mean cost per operator (fall back to
  unweighted node count when no timing is available)
- span = longest path through the dependency DAG under the same weights
- report `work`, `span`, `max_speedup = work/span`, and the achieved
  `speedup / max_speedup` as **parallel efficiency against the attainable
  bound**, not against W

This reframes the headline question. A 4-case x 150-combo parameter sweep is
enormously wide (work/span in the thousands) and should scale until bandwidth
stops it. A single-case segmentation pipeline is a nearly linear chain and
**cannot** scale, at any engine quality. "24x on every analysis" is therefore
not an engine goal -- it is only meaningful for programs whose work/span
exceeds 24, and the honest deliverable is: *the engine achieves a stated
fraction of each program's own ceiling.*

## 4. Factor matrix

Vary, in decreasing order of established importance:

1. `itk_threads` in {1, 2, 4, 24} -- the dominant factor found so far (62s vs 208s at fixed W)
2. `workers` in {1, 2, 4, 8, 12, 18, 24}
3. interpreter in {GIL, free-threaded}
4. workload class (sec 6) -- **wide** sweep vs **narrow** single-case pipeline
5. host in {fmt-5000 24-core Linux, Mac 6P+12E}

Full crossing is ~1000 runs; prune by stage:

- **Stage 1 (mechanism):** fix `workers=18`, sweep `itk`, full instrumentation.
  Answers *why* itk=1 differs in kind. Cheap: 7 runs.
- **Stage 2 (headline curve):** fix `itk=1`, sweep `workers`, both
  interpreters. Yields speedup-vs-serial and the bandwidth turnover point.
- **Stage 3 (generalisation):** repeat Stage 2's best/default/worst points on a
  narrow workload and on the second host. This is what licenses -- or refuses
  -- any claim about "every analysis".

## 5. Protocol (non-negotiable)

Derived from `engine/calibration.py`, which already argues all of this, plus the
failures in sec 0:

- **Idle gate.** Refuse to measure unless load average < 0.3 x cores AND no
  foreign compute process is running. Explicitly check for GPU/LLM servers
  (`ollama`, `llama_cpp`, `lmstudio`) -- failure #2 was caused by exactly this.
- **AC power + sleep inhibition** on laptops (`caffeinate -is`). Failure #2
  included a 2h20m hibernation inside one timed run.
- **Interleaved, not blocked.** Round-robin cells across reps so drift biases
  all cells equally.
- **Min-of-N, N>=3.** Interference can only slow a run down.
- **Reject, do not average, impossible cells.** A cell whose wall exceeds ~3x
  its cohort is contaminated, not slow; investigate it rather than folding it
  into a mean.
- **Report wall AND cpu/wall AND saturation together.** A table of wall-clock
  alone is not to be published or acted on.
- **Serial baseline must be genuinely serial**: `workers=1` AND `itk=1`.
  Anything else makes the denominator parallel (failure #1).
- **Confounds must be stated.** Current known one: the GIL venv carries
  numba 0.64/SimpleITK 2.5.2 while the free-threaded venv carries 0.66/2.5.5,
  because no older release ships cp314t wheels. GIL-vs-FT comparisons are
  therefore not single-variable and must say so.

## 6. Workload classes

The two classes have opposite parallelism structure and must both be measured:

- **WIDE** -- `_bench_scaling.imgql`: 4 cases x (3x5x5 + open radii) combos,
  ~26.3k nodes. Near-embarrassingly parallel. Upper bound on what the engine
  can show.
- **NARROW** -- a single case, single parameter set, full pipeline. Long
  dependency chain, little concurrency. Lower bound, and the honest answer for
  an interactive one-case run.

Report both. A claim of the form "the engine scales Nx" without naming the
class is meaningless.

## 7. Acceptance criteria

The engine's parallelism is "working properly, automatically, on every
analysis" iff, with no user-supplied flags:

1. `saturation >= 0.8` on the WIDE workload at the calibrated worker count;
2. achieved speedup >= 0.7 x `work/span` bound on WIDE, or the shortfall is
   attributed to a measured cause (bandwidth, per-node overhead);
3. the NARROW workload is no slower than a serial run (parallel machinery must
   not cost anything when there is nothing to parallelise);
4. defaults alone achieve the above -- any configuration needed to reach it is
   a bug in the defaults, not documentation;
5. every claim above is backed by min-of-3 on an idle host with cpu/wall and
   saturation reported alongside wall.

Nothing in this repo currently demonstrates 1-5; criterion 4 is known violated
(the shipped `cores // workers` cap lands on the worst region of the itk curve).
