# The unused cores are one phase, and that phase is bounded by one thread

**Target.** The systemic reason the machine is not fully used — not a primitive,
not ITK. The user's hypothesis was scheduling and starvation. It is right, and
the shortfall localises to a specific phase with a specific cause.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 cases, 16 of 16 goals and `dice_best_mean = 0.8513501430954795` in every run,
`--threads 32`, cold store per run, host `fmt-5000` idle, 24 cores (8 P at
5.6–5.8 GHz, 16 E at 4.6 GHz). Reports here; `--measure-period 0.2`–`0.25` and
`--measure-series` for the timelines.

## 1. When threads are runnable, the machine IS full

The report now pairs each thread census with the CPU rate of the interval
ending at it, because the aggregates looked contradictory: 31.75 threads
runnable on average against 18.6 cores' worth of CPU.

| threads runnable | intervals | mean CPU | max | share of 2400% |
|---|---|---|---|---|
| 0–4 | 2 | 443% | 711% | 18% |
| 5–12 | 3 | 1434% | 2360% | 60% |
| 13–20 | 1 | 890% | 890% | 37% |
| 21–28 | 2 | 2221% | 2339% | 92% |
| **29–40** | **13** | **2131%** | **2400%** | **89%** |
| 41+ | 6 | 1903% | 2350% | 79% |

So there is no failure to hand cores to runnable threads: whenever 29–40 are
runnable the machine sits at 89% of the ceiling and touches 2400% exactly. The
shortfall is entirely in the intervals with few runnable threads. Threads in
uninterruptible sleep average **0.14** across the run, so it is not the disk.

## 2. Where those intervals are

Per-second timeline, 27.7 s run:

```
0–1 s     644 728 1364                      startup ramp
1–21 s    2113 … 2399, dipping to 1400–1900 saturated
22–26 s   473 379 204 1567 996 749 708 912 976 792 1012 864 960 822 816 989 932
```

The last ~5.6 s — the export phase — runs at **800–1000%**, and that is 13% of
the wall clock: at the ceiling it would take 2.1 s instead of 5.6 s.

## 3. And it is NOT "nothing to run"

The same intervals, with the event-loop thread beside them:

| t | CPU | **loop thread** | runnable | in flight |
|---|---|---|---|---|
| 23.5 | 912% | **96%** | 31 | 31 |
| 23.8 | 976% | **104%** | 31 | 31 |
| 24.3 | 1012% | **100%** | 29 | 31 |
| 24.5 | 864% | **100%** | 32 | 31 |
| 25.0 | 822% | **103%** | 29 | 31 |

Thirty-one threads runnable, thirty-one kernels in flight, and the machine
delivers eight to ten cores — because the asyncio thread is pinned at a full
core. **The tail is event-loop-bound, not work-starved.**

Phase attribution, differencing the cumulative loop counters across the two
windows (`finish` and `fin_complete` overlap, so read the larger):

```
saturated 6–20 s (13.9 s wall)   NodeTable.complete  10.29 s   5208 calls
export tail 22–26 s (4.0 s wall) NodeTable.complete   3.40 s   2647 calls
```

In the tail the loop spends **3.40 of 4.0 seconds inside
`NodeTable.complete`** — 85% of a core in one function, at 1.28 ms per call over
2,647 completions of small values.

## 4. Which corrects an earlier negative result

`doc/dev/measurements/2026-09-09-loop-copy-move/` moved the payload copy off the
loop and concluded it "bought nothing", from total wall time. That verdict was
measured on the wrong phase: the saturated phase is 75% of the run and is not
loop-bound, so a whole-run average cannot see a tail effect. Re-measured with
the copy on the worker and looking only at the tail, the tail's CPU rises from
750–1010% to 1216–1532% in several intervals — and **the loop is still at
96–100%**. Wall time is 27.0 s against 27.2 s: unchanged, as before.

So the copy is *part* of the tail's loop load and not the whole of it. Removing
it does not free the tail, which means the tail's limit is the aggregate of
everything one thread must do per completion, not any single line of it.

`--no-write-cache` was tried as an isolation and is not one: it drops writes at
the BACKEND, while `AsyncPersister.submit` — and its copy — still run. (It also
turns every eviction into a recompute: 20,233 of them, and 120.2 s instead of
27.2 s. Worth knowing before anyone uses it as a control.)

## 5. What this means

**The engine's completion path is single-threaded, and that is the systemic
ceiling.** It costs nothing in a phase whose nodes are 35 ms of kernel each; it
costs 13% of the wall clock in a phase of many small nodes, where 1.28 ms of
loop bookkeeping per node cannot feed 24 cores. Neither thread count nor ITK
nor the disk nor bandwidth changes that, which is why every experiment aimed at
them returned zero or worse.

Two directions follow, and they are different in kind:

1. **Fewer, larger completions in that phase.** The export tail is 2,647
   completions in 4 s of small per-slice values. Fusion already exists to make
   several nodes one kernel; extending its coverage to that phase attacks the
   node count rather than the per-node cost.
2. **Completion work off the loop.** Everything in `_finish` after the value
   exists — accounting, the persist decision, evict-candidate tracking,
   bandwidth accounting — is bookkeeping that does not have to run on the
   thread that dispatches. That is a larger change and it needs its own
   correctness argument, since the graph state it touches is what makes
   dispatch decisions correct.

Also recorded, from the worker sweep (`../2026-09-09-worker-sweep/`, three
repetitions each): 32 workers is genuinely the fastest — 26.3 s against 28.1 at
20, 29.3 at 16, 29.0 at 8, 32.1 at 4. The "plateau from 20 upward" reported from
single runs does not survive repetition, and no default change is warranted.
