# The ITK width conclusion on record is false on this scheduler — and CPU% is anti-correlated with speed

**Target.** `engine/itk_threads.py` carries a conclusion measured on a previous
engine: "itk=24 wins at 8 workers, itk=1 wins at 18". Every later argument that
used an ITK=1 measurement rests on it. Re-measured on the current scheduler
rather than trusted.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, 16 goals, `--threads 32`, cold store on real disk per run,
host `fmt-5000` (24 cores, idle). Two repetitions per width, interleaved by
repetition. All eight runs 16 of 16 goals. Commit `1910a31`.

**Method.** `--measure`, totals from `getrusage` at start and exit; work counters
in the same report.

## Result

| ITK width | wall (2 reps) | mean | mean CPU of 2400% | recomputes |
|---|---|---|---|---|
| default (ITK's own) | 28.3, 28.0 | **28.2 s** | 1859% | 312, 342 |
| 24 | 29.3, 27.6 | **28.5 s** | 1834% | 716, 384 |
| 4 | 49.2, 49.4 | **49.3 s** | **777%** | 487, 379 |
| 1 | 49.3, 50.1 | **49.7 s** | **2030%** | 177, 151 |

**The recorded conclusion is refuted.** ITK=1 is not the winner at high worker
counts: it is 76% slower than leaving ITK alone. Anything argued from an ITK=1
measurement on this engine needs re-checking, including several of my own
comparisons this week.

**And the more important result: CPU utilisation is anti-correlated with speed
on this workload.** The fastest configuration runs at 1859% and the slowest but
one at 2030%. Optimising for "get to 2400%" would select ITK=1 and make the run
76% slower; optimising for CPU% would also reject ITK=4, but for the wrong
reason — it is slow AND idle, which is at least honest.

So the objective has to be **wall time at constant work**, with CPU% and the
work counters as diagnostics rather than as targets. The three interventions
measured today all illustrate it: moving the loop's payload copy raised CPU by
55 points and changed no wall time; adaptive ITK width raised CPU to 2062% and
made the run 75% slower; ITK=1 gives the highest CPU of all four widths here and
the second-worst time. A CPU number with no work number beside it cannot tell
those apart from a real improvement.

## Consequence for the ITK question

Leave ITK alone. `engine/itk_threads.py` already says that the untouched default
is "never the worst option, just not always the best", and on this scheduler it
is also the best of the four. The stale sentence in its docstring should be
updated to point here; the module's refusal to pick a value for itself remains
right, and calibration sweeping the value remains the only sound way to change
it on a new host.
