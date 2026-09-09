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

---

## The controlled experiment: injecting a known delay into the loop

The mechanism above was measured but its *magnitude* was not. So a busy wait of
a known duration was added to the loop's per-completion path — a busy wait, not
a sleep, because a sleep yields the loop and would measure something else — and
nothing else was changed. `d0.json`, `d600.json`, `d1200.json`, `d2400.json`;
all four runs 16 of 16 goals with `dice_best_mean = 0.8513501430954795`.

| Δ per completion | wall | cores busy | loop occupancy | kernels | CPU-s |
|---|---|---|---|---|---|
| 0 | **26.8 s** | **18.54** | 68% | 15,086 | 496 |
| +600 µs | 29.8 s | 16.59 | 81% | 15,068 | 494 |
| +1200 µs | 37.4 s | 13.46 | 88% | 15,019 | 504 |
| +2400 µs | **52.7 s** | **9.95** | 92% | 15,027 | 525 |

**Adding 2.4 ms of work to one thread nearly doubles the run — 26.8 s to
52.7 s — with the kernel count identical (15,086 against 15,027) and
CPU-seconds flat (496 against 525).** Nothing else was touched: same program,
same data, same cores, same ITK, same store, same results to sixteen digits. The
loop is the constraint, and this is not an inference from a correlation.

Utilisation falls monotonically and the loop's own occupancy rises to meet the
delay, 68% → 92%, which is the shape a single server has: the first increments
are absorbed by slack, and only then does the queue behind it cost throughput.

### Where the simple model was wrong, and what the right one is

The naive form — utilisation scales as `L/(L+Δ)` with L = 1.18 ms — over-predicts
the loss (12.3 cores predicted against 16.6 measured at Δ = 600 µs), because it
assumes the loop's occupancy is fixed. Using the served rate
`occupancy/(L+Δ) × D` instead, with D = 32.2 ms fitted at Δ = 0:

| Δ | predicted | measured |
|---|---|---|
| +600 µs | 14.7 | 16.6 |
| +1200 µs | 11.9 | 13.5 |
| +2400 µs | 8.3 | 10.0 |

Still 13–20% under, and the residual has a measured explanation rather than a
free parameter: **D is not constant.** With fewer kernels in flight there is
less contention, so each kernel finishes faster — exactly the effect measured in
`../2026-09-09-nested-parallelism/`, where the same kernel cost four times more
per call at 32 threads on 8 cores than at 8. So the model is right in form,
under-predicts because it holds D fixed, and the direction of its error is the
one that measurement independently predicts.

### What the fix is worth, in seconds

Near the current operating point the four points give about **5 seconds of wall
clock per millisecond of L**. L is 1.18 ms per completion, of which `submit` —
the payload copy — is 94–96% (`../2026-09-09-submit-is-the-cost/`). So removing
that cost from the loop is worth roughly **5 s of a 26.8 s run, about 19%**, and
that is the size of the prize rather than a hope.

What it may NOT be bought with: more work elsewhere. Relocating the copy to the
worker removes it from the loop and raised energy 2.6% and recomputes 37% for no
wall gain (`../2026-09-09-copy-location-closed/`). The two routes that remain
consistent with all of the above are paying L fewer times (fewer, larger nodes)
or making the copy unnecessary (a payload the writer can compress without
racing ITK's frees — e.g. one the engine owns rather than aliases).

The injection point stays in `core.py` as `_LOOP_DELAY_NS = 0`, so this
experiment is one `sed` away from being repeated.
