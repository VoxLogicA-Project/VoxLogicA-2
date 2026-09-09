# The payload copy's location is closed: it costs energy and buys no wall clock

**Target.** `submit` is 94–96% of `NodeTable.complete` and `complete` is the
event loop's largest phase, so relocating the payload copy from the loop to the
worker that produced the value looked like the change the tail needed
(`../2026-09-09-submit-is-the-cost/`, which stated the prediction and the
falsification condition before this was run). This is the properly-controlled
test: three repetitions of each variant, the same fixed mixed task, judged on
wall clock AND on energy.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 cases, `--threads 32`, cold store per run, host `fmt-5000` idle. All six runs
16 of 16 goals with `dice_best_mean = 0.8513501430954795`. Baseline `0a23813`;
the variant is that same tree with `_should_snapshot` installed.

## Result

| variant | wall (3 reps) | mean | **CPU-s** | **CPU ms/completion** | kernels | recomputes |
|---|---|---|---|---|---|---|
| copy on the loop | 28.6, 27.4, 26.1 | **27.4 s** | **502** | **32.96** | 14,984 | 562 |
| copy on the worker | 27.1, 30.7, 27.2 | **28.3 s** | **515** | **33.88** | 14,970 | 768 |

- **Wall time does not improve**: 28.3 s against 27.4 s, i.e. slightly worse,
  with overlapping spreads (26.1–28.6 against 27.1–30.7).
- **Energy rises**: +13 CPU-seconds (+2.6%) and +0.92 CPU-ms per completion
  (+2.8%). Same kernels, so this is not more work being done — it is the same
  work costing more.
- **Recomputes rise 37%**, 562 → 768, which is where the energy goes: the copies
  raise memory pressure between the worker and `_finish`, values are evicted,
  and evicted values are rebuilt.

The prediction in the previous note said explicitly: *"if `cpu_seconds` rises or
recomputes rise, the change is buying wall time with energy and is to be
reverted."* Both rose and the wall clock did not even improve. **Reverted, and
the mechanism is now closed** — measured twice, the second time phase-aware and
energy-aware, which is what the first measurement lacked.

## What that means, and what it does not

It does NOT mean the loop is not the tail's constraint. That stands: 31 threads
runnable, 31 kernels in flight, the asyncio thread at 96–104% of a core, 3.16 of
4.0 seconds inside `submit`. What is closed is one *route* to relieving it —
paying the same copy on another thread — because the copy's cost is not the only
thing that moves when you relocate it.

**So the remaining route is to pay it fewer times, not elsewhere.** The tail is
2,643 submits in 4 seconds of per-slice values. Halving the number of
completions in that phase halves everything the loop does per completion at
once — the copy, the accounting, the graph bookkeeping — and cannot raise memory
pressure, because fusing nodes removes intermediates rather than adding live
copies. That is `engine/fusion.py`, and the question to measure first is what it
covers in that phase today.

## A note on one metric used here

"Tail seconds" (the run of trailing intervals below 55% of the ceiling) read
1.9 s against 0.1 s, which is not consistent with the ~5.6 s tail measured from
the timeline in `../2026-09-09-tail-is-loop-bound/`. That metric only counts the
CONTIGUOUS trailing run, so a single interval above the floor terminates it, and
it is too brittle to carry an argument. It is not used in the verdict above; the
verdict rests on wall clock, CPU-seconds and CPU-ms per completion.
