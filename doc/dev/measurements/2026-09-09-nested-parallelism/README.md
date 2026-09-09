# Two nested parallelisms, and a correction: the per-kernel blow-up is oversubscription, not bandwidth

**Target.** The previous note
(`doc/dev/measurements/2026-09-09-bandwidth-bound/`) concluded that the workload
is memory-bandwidth-bound, from the observation that the same kernel costs 13×
more per call at 32 threads than at 1. Two other explanations produce the same
signature and had not been tested: the hybrid core mix (8 P-cores at 5.6–5.8 GHz,
16 E-cores at 4.6 GHz, `lscpu -e` confirms CPUs 0–7 and 8–23), and plain
oversubscription. This separates them with `taskset`.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 cases, 16 of 16 goals and `dice_best_mean = 0.8513501430954795` in every run,
cold store per run, host `fmt-5000` idle. One run per configuration.

## Result

| configuration | wall | CPU | Σ kernel durations | `volume` ms/call | `geq_sv` ms/call |
|---|---|---|---|---|---|
| 8 workers, pinned to 8 P-cores | 43.0 s | 746% | 274 s | 12.0 | 19.3 |
| 8 workers, pinned to 8 E-cores | 55.1 s | 750% | 364 s | 13.8 | 21.0 |
| **8 workers, unpinned (24 cores)** | **29.1 s** | **1696%** | **178 s** | **10.7** | **12.8** |
| **32 workers, pinned to 8 P-cores** | **42.2 s** | 732% | **1112 s** | **39.8** | **63.0** |
| 16 workers, pinned to 16 E-cores | 34.6 s | 1308% | 447 s | 19.2 | 26.4 |

### The correction

**32 workers on the same 8 P-cores take the same wall time as 8 workers on them
(42.2 s against 43.0 s) while every per-kernel figure quadruples** — `volume`
12.0 → 39.8 ms, the summed kernel durations 274 → 1112 s. Same cores, same data,
same work, four times the threads: nothing about memory changed. This is
timesharing arithmetic, and it exposes a flaw in how I read the earlier number:
**`compute_ms` is the kernel's WALL time, not its CPU time.** A kernel sharing a
core with three others reports four times the duration while doing identical
work, so the "13× more CPU per call" was mostly a wall-clock measure of
oversubscription. The bandwidth conclusion in that note is **withdrawn**; what
survives is that per-kernel figures are only comparable at equal concurrency,
which that note also says.

Core type is real but small: 43.0 s on P-cores against 55.1 s on E-cores at
equal worker count, a factor of 1.28, close to the 1.22 clock ratio. So the
machine's 2400% is not 24 equal cores — in P-core-equivalents it is about
8 + 16/1.28 ≈ 20.5, i.e. **~2050%**. A target of 2400% was asking for capacity
the machine does not have in the units the number is printed in.

### The systemic finding

**8 unpinned workers reach 1696% CPU.** Eight worker threads cannot occupy
seventeen cores, so the work is being spread from INSIDE the kernels: ITK
threads each filter across the machine. The engine's pool and ITK's pool are two
nested parallelisms, and what lands on 24 cores is their product — up to 32 × 24
runnable threads with the current defaults.

That reframes the whole thread question. The engine sizes its worker pool as
though each kernel were single-threaded, which is why the thread sweep on the
correct program is flat from 20 workers upward:

| workers | wall | CPU | Σ kernel durations | recomputes |
|---|---|---|---|---|
| 16 | 29.4 s | 1722% | 366 s | 639 |
| 20 | 26.9 s | 1866% | 422 s | 242 |
| 24 | 28.1 s | 1800% | 535 s | 530 |
| 28 | 27.7 s | 1829% | 614 s | 401 |
| 32 | 27.1 s | 1854% | 680 s | 499 |

Wall time is flat across 20–32 within noise while the summed kernel durations
grow 61%. Every worker past the plateau adds queueing latency inside kernels and
memory pressure (each in-flight kernel holds its inputs and output resident,
which is what drives the recomputes) and buys no throughput.

**So the systemic reason the cores are not "fully used" is not that the scheduler
fails to fill them.** They are filled — by the product of two pools neither of
which knows about the other — and past a modest worker count the additional
threads convert into latency rather than work. A worker-count sweep with three
repetitions is running to fix the plateau's left edge; the default should sit
there rather than at the core count.
