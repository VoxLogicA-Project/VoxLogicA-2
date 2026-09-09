# Performance results: what was tried, what it cost, what was kept

**This is the current table. Regenerate the per-run detail with:**

```bash
.venv/bin/python tools/measure/table.py
```

Every row comes from a JSON report written by the engine itself
(`--measure <report>.json`, see `implementation/python/voxlogica/engine/measure.py`).
Reports live beside the note that interprets them, one directory per experiment.

## How to read a row, in order of authority

1. **`wall s` on a FIXED MIXED TASK is the verdict.** All timings here are the
   same program — `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
   20 BraTS cases, 16 goals — so wall time compares like with like. Nothing else
   here is the verdict.
2. **`CPU-s` is proportional to ENERGY.** A shorter run bought with more
   CPU-seconds is a heater, not an optimisation. Two changes measured on
   2026-09-09 shortened nothing and raised this; one raised it by 66% for a 15%
   wall gain.
3. **`CPU ms/compl` is the energy cost of one unit of work** and must not rise.
4. **`kernels` and `recomp` are the work.** A CPU or wall improvement with more
   kernels or more recomputes is a regression wearing a better number — the
   trap that caught three interventions in one day.
5. **`compl/s` is a DIAGNOSTIC, not a score.** It is confounded by node size: a
   phase of many small nodes has a high rate and does less work. It answers one
   question only — did a change to the completion path move the completion
   path — and must never be compared across programs or phases.
6. **`ctx sw` (involuntary switches)** catches a parallelisation that trades one
   thread's time for scheduler churn.
7. **`goals` gates all of it.** A run that did not resolve every goal has no
   timings worth reading; an aborted sweep looks fast, and one was once reported
   as the fastest of three for exactly that reason.
8. **CPU% is not on this list.** On this workload it is *anti-correlated* with
   speed: the fastest ITK width runs at 1859% and the second-slowest at 2030%.

## Experiments, newest first

| date | experiment | commit | verdict | wall | effect |
|---|---|---|---|---|---|
| 09-09 | [**RAM share: the main reason**](2026-09-09-ram-share/) | `ddf40f4` | **+74% throughput** | — | On the REAL 60-case sweep, changing `_RSS_SHARE` from 0.75 to 0.40: **304 -> 529 node/s**, CPU 1986% -> 2208%, RSS 30.4 -> 19.6 GB. At 75% of RAM the box is left with 10 GB and kernels enter DIRECT reclaim (6-15k pages/s), doing the OS's page scanning on worker threads instead of computing. Every earlier measurement used the 20-case program, which never exceeds 12 GB and therefore could not see this. |
| 09-09 | [**Alias, not copy**](2026-09-09-alias-not-copy/) | `836357b` | **KEPT** | **24.3 s** | The fix. Build the numpy view once on the worker, where the image is unshared and MakeUnique is a no-op, cache it, and hand the writer that alias instead of a copy: **wall −11.3%** (25.4/24.0/23.5 against 28.6/27.4/26.1), **CPU-s −3.2%**, **CPU-ms per completion −3.5%**, **recomputes −11%**, **peak RSS −7%**, loop occupancy 67% → 40%, kernels unchanged, Dice identical to sixteen digits. |
| 09-09 | [Why the workers wait](2026-09-09-why-workers-wait/) | `f040a16`, `836357b` | **cause, proven** | — | The pool's work queue is empty in every interval, so an idle kernel thread has nothing to take; the loop's occupancy is inversely correlated with the machine's (89% against 51%) over 108 intervals; and injecting +2.4 ms per completion into the loop nearly doubles the run (26.8 → 52.7 s) with identical work. The workers wait because every unit of work passes through one thread twice. |
| 09-09 | [Copy location closed](2026-09-09-copy-location-closed/) | `0a23813` | **reverted, closed** | 28.3 s | Moving the payload copy to the worker: wall 28.3 s against 27.4 s, **+13 CPU-seconds and +2.8% CPU-ms per completion**, recomputes +37%. The prediction said energy must not rise; it rose and the wall clock did not improve. The route is closed -- the copy must be paid FEWER times, not elsewhere. |
| 09-09 | [`submit` is the cost](2026-09-09-submit-is-the-cost/) | `82858ed` | **diagnosis** | 27.8 s | Inside `complete`: `submit` is 94% of it in the saturated phase and 96% in the tail (2.995 and 1.194 ms per call); `set_value` is 3-4%. The expensive statement is the payload copy. |
| 09-09 | [Tail is loop-bound](2026-09-09-tail-is-loop-bound/) | `c401f47` | **diagnosis** | 27.7 s | The whole CPU shortfall is the 5.6 s export tail at 800–1000%, and that phase is event-loop-bound: 31 threads runnable, 31 kernels in flight, asyncio thread at 96–104% of a core, 3.40 of 4.0 s inside `NodeTable.complete`. Cores are never withheld from runnable work — at 29–40 runnable the machine is at 89% of the ceiling and touches 2400%. |
| 09-09 | [Worker sweep](2026-09-09-worker-sweep/) | `b27db24` | **no change** | 26.3 s at 32 | 32 workers is genuinely fastest over three repetitions (26.3 / 28.1 / 29.3 / 29.0 / 32.1 s at 32 / 20 / 16 / 8 / 4). The "plateau from 20 up" seen in single runs did not survive repetition and was withdrawn. |
| 09-09 | [Nested parallelism](2026-09-09-nested-parallelism/) | `912d591` | **correction** | — | 32 workers pinned to 8 P-cores take the same wall time as 8 on them while every per-kernel figure quadruples: the per-kernel blow-up is oversubscription of a WALL-time measure, not bandwidth. 8 unpinned workers reach 1696% because ITK spreads each filter from inside — engine pool × ITK pool is the real width. P/E cores differ by 1.28×, so the machine is ~2050% in P-core-equivalents, not 2400%. |
| 09-09 | [Bandwidth (withdrawn)](2026-09-09-bandwidth-bound/) | `34b5767` | **withdrawn** | — | Concluded memory-bound from a 13× per-kernel cost growth. Superseded by the above: `compute_ms` is wall, not CPU. |
| 09-09 | [Ball opening](2026-09-09-ball-opening/) | `3517936` | **reported, not kept** | **16.4 s** | One ITK opening replaces two distance transforms: −42% wall, 45% fewer kernels, CPU flat. But it is a different computation — the discrete ball implies `d > r` where `distgeq` asks `d >= r`, 21,103 voxels on one case — and the Dice moves 0.8514 → 0.8612. A method choice for the experiment's owner. |
| 09-09 | [Squared distance](2026-09-09-squared-distance/) | `d7ebd22` | **KEPT** | **27.1 s** | `dt2` where only a radius is compared: −4% wall, −9.5% on the dominant kernel, CPU flat, Dice identical to sixteen digits. |
| 09-09 | [Scheduler headroom](2026-09-09-scheduler-headroom/) | `f0a751c` | **closed** | 27.7 s | 25.5 kernels in flight on 24 cores, 106% of the work-over-cores floor, kernels off-CPU 26% of their own runtime. Aggregate scheduling has no headroom left. |
| 09-09 | [ITK width](2026-09-09-itk-width/) | `6ec91ec` | **leave alone** | 28.2 s | The recorded "itk=1 wins at 18 workers" is false on this engine: ITK=1 is 76% slower *and* has the highest CPU of four widths. The default is the fastest. |
| 09-09 | [Loop copy move](2026-09-09-loop-copy-move/) | `ffc8838` | **reverted** | 28.2 s | Halved the loop's largest phase (19.5 → 9.9 s) for no wall change and +45% recomputes. Later found to have been judged on the wrong phase — it does help the tail — see the tail note. |
| 09-09 | [Loop attribution](2026-09-09-loop-attribution/) | `a7fa388` | **diagnosis** | 26.8 s | `NodeTable.complete` is 65% of the loop's CPU and 43% of the wall clock, 1.63 ms per call. Every phase the scheduler is usually blamed for is under a second combined. |
| 09-09 | [I/O and loop](2026-09-09-io-and-loop/) | `f5b629c` | **diagnosis** | 27.2 s | Disk ruled out: device 5–10% utilised, threads in uninterruptible sleep 0.00–0.09. |
| 09-08 | [scheduler-core vs incoming](2026-09-08-scheduler-core-vs-incoming/) | `d1b745b` | **no change** | 30.1 s | The two engines are indistinguishable; the wait-protocol fix costs nothing and buys correctness. |

## What is settled, and what is open

**Settled as levers, with measurement:** ITK width, worker count, aggregate
scheduling, disk, the GIL, memory bandwidth as an explanation of per-kernel cost.

**Open:** the export tail, which is the entire remaining shortfall and is bounded
by one thread doing per-completion bookkeeping. Two directions — fewer and larger
completions in that phase (fusion), or that bookkeeping off the loop, which needs
its own correctness argument because dispatch decisions depend on the graph state
it touches. Any attempt must be judged on the wall clock of this fixed task AND
on `CPU-s` and `CPU ms/compl`, or it will look like a win while burning more
energy for the same result.

## Per-run detail

Generated by `tools/measure/table.py`; run it rather than trusting a paste.
