# Squared distance: 4% of the wall clock, 9.5% off the dominant kernel, bit-identical

**Target.** `vox1.dt` was measured at 940 kernels and 348.7 CPU-seconds — 54% of
the entire kernel time of a threshold sweep, 371 ms per call
(`doc/dev/measurements/2026-09-09-loop-attribution/`). Its arguments are
genuinely distinct, so caching has nothing left to share: the question is
whether each call can be made cheaper without changing what the program computes.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, 16 goals, `--threads 32`, cold store on real disk per run,
host `fmt-5000`, 24 cores, idle. Baseline commit `f0a751c`; three repetitions of
each. All runs 16 of 16 goals.

**The argument, before the measurement.** Every consumer of `dt` in
`vox1/compat.imgql` compares it against a radius and nothing else:

```
let pdt(x)       = mask(dt(x), dt(x) >. 0)
let distgeq(x,y) = x .<= pdt(y)
let distleq(x,y) = x .>= pdt(y)
let smoothen(a,x) = distleq(x, distgeq(x, !(a)))
```

Comparing squared distances against the squared radius is exactly equivalent
there: both sides are non-negative and squaring is monotone on non-negatives. So
`SetSquaredDistance(True)` removes one square root per voxel with no change of
meaning. Added as a separate primitive `dt2` rather than a flag on `dt`, because
`dt`'s value is a distance and a program is entitled to use it as one; only
`distgeq`/`distleq` move to it, with the radius squared to match.

## Result

| | wall (3 reps) | mean | mean CPU | kernels | recomputes | dt CPU-s |
|---|---|---|---|---|---|---|
| `dt` (baseline, 5 reps) | 28.0, 27.9, 28.6, 28.3, 28.0 | **28.2 s** | 1859% | ~15,000 | 292–624 | **348.7** |
| `dt2` (squared) | 26.5, 27.0, 27.7 | **27.1 s** | 1844% | ~14,860 | 252–564 | **315.5** |

- **Wall time: −1.1 s, −4%.** The distributions barely overlap: the squared
  variant's worst run (27.7 s) beats the baseline's median (28.0 s), and the
  difference is about two baseline standard deviations (σ = 0.28 s).
- **The dominant kernel: −33 CPU-seconds, −9.5%**, which is where the wall-clock
  saving comes from.
- **Work unchanged**: same kernel count within 1%, recomputes in the same range.
  CPU% is flat, which is the correct signature of a real win on this workload —
  less work in less time, rather than more CPU.
- **`dice_best_mean` identical to all sixteen digits** — 0.8513501430954795 — on
  all three runs. The equivalence argument is not merely plausible, it is
  observed: had the monotonicity reasoning been wrong, this is where it would
  have shown.

## What is left in dt

`dt2` is still 315 of 670 CPU-seconds, 47% of all kernel time. The remaining
structure is that `smoothen` is a morphological OPENING expressed as two
distance transforms — `distgeq` erodes, `distleq` dilates — so every smoothen
costs two of them, and `segment` calls smoothen twice per (case, threshold).
That is where the next factor would come from, and it is a question about the
formulation rather than about the filter: ITK computes a binary opening by a
ball directly, which for this program's integer radii on 1 mm isotropic data
should give the same result for one filter instead of two. Equality would have
to be shown, not assumed — with anisotropic spacing or a non-integer radius the
two formulations are NOT the same, and this program's radii (2.0 and 5.0) happen
to be the easy case.
