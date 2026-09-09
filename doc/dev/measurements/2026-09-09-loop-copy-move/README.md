# A negative result: halving the loop's biggest phase changed no wall time

**Target.** Whether the payload copy taken on the event loop is the ceiling of a
sweep. The previous measurement
(`doc/dev/measurements/2026-09-09-loop-attribution/`) attributed 17.55 s of a
26.8 s run — 65% of the event loop's entire CPU — to one statement:
`_payload_snapshot(value)` inside `AsyncPersister.submit`, a full copy of every
persisted image payload, taken on the single thread that also dispatches every
kernel. That note ended with a prediction, written before the change:

> the loop loses ~17.5 s of work per 27 s run, so loop occupancy should fall
> well below the 25% band where the process was measured at 2250–2369%, and wall
> time should fall accordingly. […] If wall time does not improve, the
> attribution above is wrong and this note says so.

**Wall time did not improve. The prediction is falsified and this is that note.**

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, 16 goals. Host `fmt-5000`, 24 cores, `--threads 32`, store
on real disk, cold store per run, deleted before and after. Three repetitions
per variant. Baseline commit `2b01604`. All nine runs reached 16 of 16 goals.

**Method.** `--measure` (`engine/measure.py`), totals from
`getrusage(RUSAGE_SELF)` at start and exit, work counters (`kernels_executed`,
`recomputes`) in the same file — added deliberately *before* the change, because
a change that raises utilisation by doing more work reads as an improvement
unless the work is in the table.

## Result

| variant | wall (3 reps) | mean | CPU of 2400% | kernels | recomputes | loop copy phase |
|---|---|---|---|---|---|---|
| copy on the event loop (baseline) | 28.0, 27.9, 28.6 | **28.2 s** | 1853, 1857, 1844 → **1851%** | ~15,000 | 292, 312, 624 → **409** | **19.5 s** |
| copy on the worker, always | 27.8, 27.8, 28.9 | **28.2 s** | 1934, 1928, 1857 → **1906%** | ~15,000 | 633, 470, 840 → **648** | **9.9 s** |
| copy on the worker, gated on backlog | 27.3, 29.4, 28.6 | **28.4 s** | 1932, 1804, 1863 → **1866%** | ~15,000 | 417, 754, 609 → **593** | **11.0 s** |

The intervention worked as intended and produced nothing:

- **The loop's largest phase halved**, 19.5 s → 9.9 s, exactly as designed.
- **Wall time did not move**: 28.2, 28.2, 28.4 s, against a within-variant spread
  of 27.3–29.4 s. There is no effect to detect here.
- **CPU utilisation ROSE**, 1851% → 1906%, and that is the trap: read alone it
  looks like a 55-point improvement in saturation.
- **Recomputes rose 45%**, 409 → 648, for the same ~15,000 kernels. The copies
  are live between the worker and `_finish`, and that pressure evicts values
  that then have to be rebuilt. The higher CPU is the recomputes.

So the higher CPU number is a regression wearing a better number, and it is the
reason the work counters exist.

## What this rules out, and what it does not

**Rules out:** the event loop's payload copy as the binding constraint at this
scale. The loop was 65% occupied by it and had enough slack that removing half
made no difference to throughput. By extension it weakens the wider claim from
the io-and-loop measurement: the dose-response there (process at 791–1086% while
the loop is at ≥90%) is real as a description of stalls, but it does not follow
that lowering the loop's AVERAGE load raises throughput — that inference is now
tested and false.

**Does not rule out:** the loop as the constraint during the stalls themselves,
which are a different phenomenon (a burst of synchronous expansion work, `ready`
pinned at 117 and dropping to 33 the moment it clears) and are not addressed by
making a per-value copy cheaper.

**Arithmetic worth keeping.** 28.2 s at 1851% is 522 CPU-seconds for ~15,000
kernels: 35 ms of CPU per kernel. The run is not overhead-bound — it is doing
that much kernel work. Closing the gap from 1851% to 2400% is worth 6.4 s of
wall time at best, while halving the kernel count would be worth 14 s. That
reframes the next question from scheduling to fusion and common-subexpression
reuse, and it is a question the instrument can now answer per change.

## Disposition

The mechanism is kept and switched off: `Executor._snapshots`,
`Executor._should_snapshot` and `AsyncPersister.submit(snapshot=...)` remain, so
a future fix has the seam if the loop ever becomes binding, and
`ComputationEngine` sets `_should_snapshot = None` with these numbers in the
comment. The instrumentation and the work counters stay on their own merits.
