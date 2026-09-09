# Why the worker CPUs wait: measured, not modelled

**Question.** Demonstrable, unequivocal, reproducible certainty about why the
kernel threads are idle while there is work. Not a proposed cure — the cause.

**Reproduce it.** One command; the answer is in the report and the series:

```bash
tools/measure/run.sh doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql \
    /home/vincenzo/meas/pool.json -- --threads 32 --measure-period 0.2 --measure-series \
    --store-db /home/vincenzo/meas/pool.db
```

**Inputs.** The fixed mixed task: 20 BraTS cases, 16 of 16 goals,
`dice_best_mean = 0.8513501430954795`, `--threads 32`, cold store, host
`fmt-5000` idle, 24 cores. Run: 26.7 s wall, 1864% CPU, 498 CPU-seconds.

## The architecture, which is the whole explanation

`core.py` line 495: `workers = [asyncio.create_task(self._worker()) for _ in
range(self.max_concurrency)]`. **The "32 workers" are coroutines on ONE thread.**
Kernels are the only thing that leaves it: `executor.run` does
`loop.run_in_executor(self._pool, self._compute, ...)` onto a
`ThreadPoolExecutor`. So one thread performs every dispatch and every
completion, and the pool threads do nothing but run kernels.

Each worker coroutine holds **at most one** outstanding submission, so the pool
can never build a queue of work to fall back on. That is not an inference; it is
measured below.

## The measurement

108 intervals, 0.2 s apart, split by the process CPU of the interval:

| intervals | **pool backlog** | **event loop** | `in_flight` | pool threads |
|---|---|---|---|---|
| CPU < 1200% (n=29) | **0.0, max 0** | **89%** | 25.5 | 27 |
| CPU ≥ 2000% (n=79) | **0.0, max 0** | **51%** | 30.8 | 32 |

And the last seconds of the run, interval by interval:

```
   t     CPU%   loop%   backlog   in_flight
 24.4     950     100         0          31
 24.6     845     100         0          31
 25.0    1035     100         0          31
 25.4     880     101         0          31
 25.8    1000     105         0          31
 26.4    1025     100         0          31
```

Three facts, each independently sufficient:

1. **The pool's queue is empty at ALL times** — in the low-CPU intervals and the
   high-CPU ones alike, mean 0.0 and maximum 0 across every interval. So a pool
   thread that finishes a kernel has nothing to pick up. It cannot be waiting on
   a lock, on memory, or on the disk, because there is no next task for it to be
   waiting *with*.
2. **The loop's occupancy is inversely correlated with the machine's.** 89% of a
   core when the machine is under 1200%; 51% when it is over 2000%. Over 108
   intervals, in one direction.
3. **`in_flight` says 31 while the machine delivers 8-10 cores.** That is not a
   contradiction, it is the mechanism: `_in_flight` is incremented before
   `await executor.run(...)` and decremented in the `finally` after it, so it
   counts a node whose kernel has ALREADY FINISHED but whose completion the loop
   has not yet processed. With an empty pool queue and the loop at 100%, most of
   those 31 are finished kernels waiting for one thread to notice.

## The mechanism, stated so it can be checked

A node costs **L** milliseconds of the single loop thread (pop, dependency
check, dispatch, then `_finish` on the way back) and **D** milliseconds of a
pool thread (the kernel). Because each coroutine holds one submission and the
pool queue is therefore always empty, a pool thread's next kernel cannot start
until the loop has cycled that coroutine again. The loop is a single server: it
serves at most 1/L nodes per second, and each served node occupies a pool thread
for D. **Busy pool threads ≈ D/L, capped by the core count.**

Measured L, differencing the loop's own phase counters
(`../2026-09-09-submit-is-the-cost/`): 1.2 ms per completion in the export
phase, 1.9 ms in the saturated phase — of which `submit`, i.e. the payload copy,
is 94-96%.

**So the worker CPUs wait because every unit of work must pass through one
thread twice — once to be dispatched, once to be retired — and that thread
spends 1.2-1.9 ms per unit doing so.** Nothing about cores, memory bandwidth,
ITK, the disk or the GIL is involved; all six were measured and excluded
separately, and this is what remained.

## What follows, and what does not

**Falsifiable prediction:** reduce L and utilisation rises; raise L and it
falls. Half-confirmed already — moving the payload copy off the loop *did* raise
the tail's CPU from 750-1010% to 1216-1532% — but that route is closed because
it raised energy and recomputes elsewhere
(`../2026-09-09-copy-location-closed/`). The prediction stands and is worth
testing directly by INJECTING a known delay into the loop's per-node path: if
utilisation follows D/L, the model is confirmed quantitatively rather than
directionally.

**What does not follow:** that any particular cure is right. Two remain
consistent with the mechanism — fewer nodes (so L is paid fewer times) or less
work per node on the loop — and neither is established by this measurement. The
cause is.
