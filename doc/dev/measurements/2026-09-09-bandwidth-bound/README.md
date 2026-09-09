# The workload is memory-bound: the same kernel costs 13× more CPU at 32 threads than at 1

**Target.** `vox1.volume` was 41 ms per call in the sweep, for a `count_nonzero`
over an 8.9-million-voxel uint8 volume — about forty times what reading 8.9 MB
should cost. The kernel is already optimal (uint8 fast path, no intermediate,
`count_nonzero` straight off a pinned view), so either the arithmetic was wrong
or the cost is not the kernel's own.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, 16 of 16 goals in every run, cold store per run, host
`fmt-5000`, 24 cores, idle. One run per thread count. `--measure` reports;
per-operator CPU and call counts from the same file.

**Caveat on the absolute numbers, stated because it is not a small one.** These
five runs used the `ball_opening` variant of `smoothen` (Dice 0.861210078655466
rather than 0.8513501430954795): the revert had not been synchronised to the
host when they launched. Every run used the SAME program, so the scaling
conclusion — which is what this note is about — is unaffected; but the absolute
wall times belong to that variant and should not be compared with the numbers in
the other notes here.

## Result

| threads | wall | mean CPU | kernel CPU total | `volume` ms/call | `geq_sv` ms/call | `ball_opening` ms/call |
|---|---|---|---|---|---|---|
| 1 | 205.7 s | 122% | **183 s** | **1.8** | **2.8** | 365.9 |
| 4 | 55.9 s | 444% | 207 s | 4.6 | 4.6 | 387.5 |
| 8 | 29.7 s | 827% | 218 s | 5.7 | 6.6 | 388.4 |
| 16 | 19.4 s | 1450% | 281 s | 9.3 | 9.3 | 469.1 |
| 32 | 16.5 s | 1817% | **467 s** | **24.2** | **29.0** | 644.3 |

**The same computation on the same data costs 13× more CPU per call at 32
threads than at 1** (`volume`: 1.8 → 24.2 ms; `geq_sv`: 2.8 → 29.0 ms). Nothing
about the work changed, so the growth is contention, and for kernels that stream
a whole volume and do almost no arithmetic the contended resource is memory
bandwidth.

**Total kernel CPU grows from 183 to 467 seconds for identical work.** At 32
threads, 61% of the CPU the kernels consume is not work — it is waiting,
accounted as CPU because a stalled core is still a busy core.

`ball_opening` grows only 1.8× because ITK threads it internally and was already
sharing bandwidth across its own workers.

## What this settles

**The "reach 2400% CPU" target was never the right objective, and now there is a
number for why.** At 32 threads the engine already burns 467 CPU-seconds to
perform 183 seconds of work. Pushing utilisation higher means buying more of the
2.55× overhead, which is exactly what every intervention that raised CPU% this
week did — the loop-copy move (+55 points, no wall change), adaptive ITK width
(2062%, 75% slower), ITK=1 (2030%, 76% slower).

**It also explains the residual gap the scheduler measurement left open.** That
note found kernels off-CPU 26% of their own runtime and no dispatch decision able
to reach inside them. This is what they are waiting for.

**And it means per-kernel timings are meaningless without their thread count.**
Every per-operator figure in these notes is a figure at `--threads 32`; the same
operator at `--threads 1` is an order of magnitude cheaper per call. Any future
comparison of two kernels must hold concurrency fixed.

## What it opens

1. **Wall time is still monotone in threads here — 205.7 → 16.5 s, a 12.5×
   speedup on 24 cores — but the last doubling buys little: 16 → 32 threads is
   −15% of wall for +66% of CPU.** So there may be a better operating point, and
   the default (`--threads 0`, auto) should be measured against fixed values
   rather than assumed. That sweep is running.
2. **The only way to make a bandwidth-bound kernel cheaper is not to read the
   volume.** That is fusion, and it is now the highest-value lever on the board:
   `volume(and(a,b))` reads `and`'s output from DRAM immediately after writing
   it there. A cone that ended in a reduction would count while the data is
   still in cache and never materialise the intermediate at all. The fusion
   machinery exists but a cone produces images, not scalars — extending it to
   admit a terminal reduction is the change this measurement argues for.
