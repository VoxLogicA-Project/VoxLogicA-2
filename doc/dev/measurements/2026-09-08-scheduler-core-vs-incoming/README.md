# scheduler-core against incoming, on an idle machine

Target: mean CPU utilisation of one `voxlogica run`, and its wall time.
Inputs: `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, FLAIR only, cold store on real disk (never `/tmp`, which is
a 31 GB tmpfs on fmt-5000 and made every earlier number worthless).
Method: `measure2.sh` — 3 repetitions x 4 configurations, `--threads 32`, one
CPU sample per second from `/proc/<pid>/stat`, plus a sample per second of the
CPU of every OTHER user so a contaminated row can be discarded instead of
averaged. Each row records the goal count and the error code, because a run
that aborts early looks fast.

## Result

| variant | ITK threads | wall (s) | mean CPU | peak CPU | goals | Dice |
|---|---|---|---|---|---|---|
| incoming (`3140f83`) | 24 | 30.09 | 1898% | 2436% | 16/16 | 0.85135 |
| incoming (`3140f83`) | 1  | 51.67 | 2058% | 2469% | 16/16 | 0.85135 |
| scheduler-core (`e4435ae`) | 24 | 30.40 | 1880% | 2465% | 16/16 | 0.85135 |
| scheduler-core (`e4435ae`) | 1  | 50.96 | 2062% | 2462% | 16/16 | 0.85135 |

Wall time is the mean of three; the spread is 29.6–30.9 s for both engines at
ITK=24, so **the two engines are indistinguishable**. That is the intended
outcome: the wait-protocol fix on `scheduler-core` buys correctness (a node
registered twice no longer has its wait count overwritten), not speed.

Others' CPU was 11–14% throughout, so these rows are the first clean ones.

## What the CPU numbers actually say

The 2000% target is reached only at ITK=1 — and it costs 70% more wall time.
Forcing ITK's internal width to 1 spreads the same work over more concurrent
nodes, so occupancy rises while throughput falls. Mean CPU is therefore the
wrong objective on its own: 2062% at 51 s is worse than 1880% at 30 s.

Peak is 2400–2490% in every configuration, i.e. all 24 cores. What is missing
from the mean is the tail, not per-node parallelism: the run spends part of its
time with too few independent nodes to fill 24 cores, which is a property of
this program's dependency structure (20 cases x a threshold sweep, narrowing to
a single reduction) and not of the dispatcher.

## Void measurement, kept

`quiet.tsv` / `quiet.log` are the FIRST run of the same script and are wrong.
The `core` worktree was one commit behind, on the withdrawn speculation policy
(`a812faf`, now `origin/speculation-rejected`), which fails deterministically
with `'>' not supported between instances of 'Handle' and 'Handle'` and so
aborted at 13 of 16 goals. Its 26.3 s was not a fast run, it was a short one —
which is why every row here carries its goal count.

That table also caught something real on `incoming`: 2 of 3 runs at ITK=24 died
with `E_INTERNAL: engine finished with an unresolved goal` on
`print "outlier_dice"`, and 0 of 3 at ITK=1. It did not reproduce in the clean
run (0 of 6). Still open, still timing-dependent.

## Where the missing CPU is: the tail, and it is serial

The per-second samples answer the question the means could not. `m2_base_24_1.cpu`,
one value per second:

```
1675 2248 2239 1935 2372 2346 2266 2137 2418 2336 2246 2341 2179 2063 2259 1993
2350 2080 2241 2285 1847 | 1491 664 1028 854 1149 738 266
```

Twenty-one seconds at 1900–2420%, i.e. **19 to 24 of the 24 cores busy**, then
seven seconds at 270–1150%. `scheduler-core` is the same shape. So nothing fails
to fill the CPU during the parallel phase; the mean is an average of a saturated
phase and a starved tail, and 1900% is what 2200% for 75% of the run and 900%
for 25% of it comes to.

`tail.sh` asks what the tail is made of, by changing `outlier_count` — the
number of cases whose three planes get exported — and nothing else:

| outliers exported | wall (s) | saturated phase (s) | tail (s) | CPU during the tail |
|---|---|---|---|---|
| 2 | 26 | 21 | 5 | 1741 → 152% |
| 10 | 28 | 21 | 7 | 1491 → 266% |
| 20 | 33 | 21 | 12 | 1440, 235, 321, 215, 1905, 598 … 923% |

The tail is the export phase: its LENGTH scales with the number of cases
exported, while the saturated phase stays at 21 s. But its CPU **does not scale
with it** — twenty independent per-case chains occupy no more of the machine
than two do, and the n=20 run spends three seconds at 215–321%, which is one to
three cores.

That rules out the obvious explanation. The tail is not short of independent
work; it is serial work inside the engine. The export path is the one part of
this program that expands dynamically — `equator_z` reduces
`for zi in range(pole_low(m), pole_high(m) + 1)`, whose bounds are computed —
and expansion runs on the event-loop thread (`engine/expander.py::_reduce_chunk`
calls `reduce_expression` per element), which is also the only thread that can
dispatch. Twenty cases times some forty planes each is eight hundred body
reductions competing with dispatch for one thread.

That is a hypothesis with a measurement behind it, not a conclusion: what is
established is that the tail is serial and that its width is not the cause.
Identifying it needs the expander instrumented, which is the next step.
