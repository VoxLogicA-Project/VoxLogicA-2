# How much is left for the scheduler to win? Almost nothing, and here is the arithmetic

**Target.** Whether the gap between 1888% and 2400% CPU is the scheduler's to
close. Every performance question this week was posed as a scheduling question;
this one asks whether that framing survives its own numbers.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, 16 of 16 goals, `--threads 32`, cold store on real disk,
host `fmt-5000`, 24 cores, idle. Commit `6ec91ec`. Report: `report.json`.

**Method.** One `--measure` run. Totals from `getrusage(RUSAGE_SELF)` at start
and exit. `kernel wall` is the sum of every kernel's own measured duration
(`compute_ms`, which the engine already records to price eviction), aggregated
per operator by the report.

## The arithmetic

```
wall clock                              27.7 s
CPU actually consumed                   523 s      = 1888% of 2400%
sum of every kernel's own duration      707 s      over 14,879 kernels
```

Three ratios follow, and they settle the question:

| quantity | value | what it means |
|---|---|---|
| `kernel_wall / wall` | **25.5** | kernels in flight on average, on **24 cores** |
| `(kernel_wall / cores) / wall` | **106%** | the run is FASTER than work-over-cores |
| `cpu / kernel_wall` | **0.74** | a running kernel is on-CPU 74% of its own runtime |

**The scheduler is keeping the machine full.** 25.5 kernels in flight on 24
cores is not starvation, it is slight oversubscription; and the run beats the
naive floor of work-over-cores (29.5 s against a measured 27.7 s) because
kernels overlap and ITK's own threads take up slack inside them.

**The missing CPU is inside the kernels, not between them.** 0.74 says that a
kernel which the engine believes is running is off-CPU for a quarter of its
life — stalled on memory, or inside ITK's own synchronisation. Multiply it out:
523 / 0.74 = 707, and 707 / 27.7 = 25.5 cores' worth of kernel *residency*
against 18.9 cores' worth of *computation*. The 5.1-core gap between 1888% and
2400% is that 0.26, and no dispatch decision reaches inside a kernel to recover
it.

For completeness, the shape agrees rather than contradicting: 49% of sampled
intervals are at or above 90% of every core, 21% are below half. The low
intervals are the export tail and the loop stalls, both previously measured, and
together they are worth about 6 s of wall time if they vanished entirely —
against 14 s for halving the kernel count.

## What this closes and what it opens

**Closed:** "the scheduler is not filling the machine". It is. Further dispatch,
queueing or eviction work on this program has at most a few percent in it, and
the two interventions attempted today (moving the loop's payload copy, adaptive
ITK width) returned 0% and −75% respectively.

**Open, in order of measured value:**

1. **`vox1.dt`: 940 kernels, 348.7 CPU-s, 371 ms each — 54% of all kernel time.**
   It is `SignedMaurerDistanceMapImageFilter` over a boolean region, called from
   `smoothen` via `pdt(x) = mask(dt(x), dt(x) > 0)` once per (case, threshold).
   The arguments are genuinely distinct, so content addressing has nothing left
   to share: this is an algorithmic question, not a caching one. Squared
   distance would remove a sqrt pass over every voxel and is exactly equivalent
   wherever the result is only compared against a radius — which is what
   `smoothen` does. That is the single biggest lever on the board.
2. **Kernel memory behaviour**, the 0.26 above. dt is memory-bound and its
   threaded path was measured today to burn three times the CPU for the same
   work, so the direction is fewer bytes touched, not more threads.
3. The tail and the loop stalls, worth ~6 s together, and already characterised.
