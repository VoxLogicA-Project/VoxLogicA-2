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
