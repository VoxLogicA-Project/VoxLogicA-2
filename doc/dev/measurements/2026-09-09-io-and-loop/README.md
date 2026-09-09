# Is the disk the ceiling? No: the event loop is, and the disk is at 9%

**Target.** Where the missing CPU goes in one `voxlogica run` on a 24-core host:
the sweep holds 1864–1914% of an available 2400% and is at ≥90% of all cores in
about half the sampled intervals. Candidates on entry: block-device I/O wait,
the single-threaded asyncio event loop, or neither.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`
— 20 BraTS2020 cases, FLAIR only, a 21-point threshold sweep per case, 16
goals. Host `fmt-5000`, 24 cores (Intel), 61 GB RAM, store and measurements on
`/home/vincenzo/meas` → `/dev/nvme0n1p2` → device `nvme0n1` (never `/tmp`,
which is a 31 GB tmpfs on this host and has invalidated a whole day of numbers
before). No other `voxlogica` process of ours was running; foreign CPU was 3%
at launch and `run.sh` held each run until foreign CPU had been under 100% of
one core for 60 s.

**Method.** `tools/measure/compare.sh`, 3 repetitions × 4 configurations,
INTERLEAVED (`A B C D, A B C D, A B C D`), every run at `--threads 32` with its
own cold store deleted before and after. Commit `b27db24`, clean tree.
Instrumentation is `implementation/python/voxlogica/engine/measure.py`: the
process samples itself on its own thread at 4 Hz; totals come from
`getrusage(RUSAGE_SELF)` read once at start and once at exit, so the headline
CPU figure has no sampling error at all; every rate in the tables comes from
consecutive sample timestamps. Each sample carries `/proc/self/io`,
`/proc/diskstats` for `nvme0n1`, and — every 4th sample — a census of every
thread's R/S/D state. The instrument's own cost is measured per run with
`RUSAGE_THREAD`: **0.066–0.077% of run CPU**, i.e. 0.3–0.4 CPU-seconds out of
390–520.

Regenerate the tables from the files here:

    .venv/bin/python tools/measure/report.py doc/dev/measurements/2026-09-09-io-and-loop/*_rep*.tsv
    .venv/bin/python tools/measure/attribution.py doc/dev/measurements/2026-09-09-io-and-loop/*_rep*.tsv

`report.txt` and `attribution.txt` are those outputs as run.

## Result

Means of three repetitions. `loop CPU-s` is the CPU consumed by the asyncio
thread alone; `dev busy` is `/proc/diskstats` io_ticks, the time the device had
at least one request in flight.

| config | flags | goals | wall s | mean CPU of 2400% | process writes | device writes | dev busy | dev util | mean thr in D | loop CPU-s |
|---|---|---|---|---|---|---|---|---|---|---|
| normal | (a real store) | **16/16** | 27.2 | **1895%** | 5.71 GB | 4.17 GB | 2.18 s | 5–10% | 0.04–0.08 | 17.2 |
| nowritecache | `--no-write-cache` | 13/16 | 23.8 | **1800%** | 0.00 GB | 0.00 GB | 0.09 s | 0% | 0.00–0.09 | 17.5 |
| nocache | `--no-cache` | 13/16 | 18.8 | **2244%** | 0.00 GB | 0.00 GB | 0.05 s | 0% | 0.00–0.50 | 2.1 |
| sparse | `--sparse-cache` | 13/16 | 24.8 | **1972%** | 3.90 GB | 0.10 GB | 1.22 s | 2–10% | 0.03–0.40 | 13.9 |

**Only `normal` completed.** The other three configurations resolved 13 of 16
goals on 3 of 3 runs each — deterministic, not the intermittent failure this
experiment was warned about — so their WALL AND CPU FIGURES ARE NOT COMPARABLE
WITH `normal`'s and are not used as such below. Their I/O columns are valid
readings of the window that was measured, and that is what they are used for.
See "The blocker" at the end.

### The disk is not the ceiling

Three independent measurements, any one of which is sufficient:

1. **The device is idle 90–95% of the time in the run under investigation.**
   `normal` keeps `nvme0n1` busy for 2.18 s of 27.2 s — 5–10% utilisation —
   while writing 4.17 GB to it. Utilisation, not throughput, is the saturation
   test: a device is pinned at 100% by a 3 MB/s trickle of small synchronous
   writes as surely as by a streaming 2 GB/s. This one is not pinned.
2. **Our threads are essentially never waiting for it.** Across 277 thread
   censuses over all twelve runs, the mean count of our own threads in D state
   (uninterruptible sleep, i.e. blocked in the kernel on I/O) is 0.00–0.09 in
   ten of the twelve — the exceptions, 0.38 and 0.50, are both INCOMPLETE runs
   — and the maximum ever seen in a `normal` run is **1 thread** out of 136. A
   thread in D is a thread not on a CPU, so the whole budget the disk
   hypothesis has is `mean(threads in D) × the measured span` = 1.1–2.2
   core-seconds against a shortfall of 128–147 core-seconds: **0.8–1.7%**.
   This contradicts the `htop` screenshot that motivated the experiment, which
   showed several threads in D at 13–33 MB/s each. The number wins: that was a
   sample of one instant, and over 27 s of 1 Hz censuses it does not reproduce.
3. **Removing every byte of writing makes CPU WORSE, not better.**
   `--no-write-cache` writes 0.00 GB — confirmed on both counters, process
   `write_bytes` and device sectors — and reaches 1800%, which is 95 points
   BELOW the 1895% of the run that writes 5.71 GB. Within `normal` itself the
   device write volume varies 2.1× across the three repetitions (5.32, 4.68,
   2.51 GB) and mean CPU moves the wrong way with it (1913.6%, 1906.9%,
   1863.8%): the run that wrote the LEAST got the least CPU. That comparison
   uses only complete runs.

### The event loop is the ceiling, and it is a dose-response

Process CPU falls monotonically with how much of one core the asyncio thread
held, in **all twelve runs**, at device write rates spanning 0 to 199 MB/s.
Duration-weighted mean process CPU by loop band (`attribution.txt`):

| loop thread CPU | normal | nowritecache | nocache | sparse |
|---|---|---|---|---|
| 0–25% | 2291–2369% | 2250–2356% | 2288–2324% | 2323–2336% |
| 25–50% | 2196–2312% | 2214–2326% | 1827–2023% | 2222–2282% |
| 50–75% | 2124–2193% | 1547–2118% | 1296–1423% | 2036–2065% |
| 75–90% | 1568–1794% | 1615–1729% | (no time) | 1377–1497% |
| ≥90% | 791–1086% | 1579–1686% | (no time) | 885–963% |

When the loop holds a whole core, the other 23 cores deliver between 7 and 10
of themselves. In the three complete `normal` runs, **66–82% of the shortfall
occurs while the loop thread is above 75% of a core**, in 8.6–10.6 s of a 27 s
run.

And the cross-configuration ordering is the loop's, not the disk's:

| config | loop CPU-s | mean CPU | device writes |
|---|---|---|---|
| nocache | 2.1 | 2244% | 0.00 GB |
| sparse | 13.9 | 1972% | 0.10 GB |
| normal | 17.2 | 1895% | 4.17 GB |
| nowritecache | 17.5 | 1800% | 0.00 GB |

Mean CPU orders perfectly by loop CPU-seconds (4 of 4, descending) and not at
all by bytes written (0.00, 0.10, 4.17, 0.00). `--no-cache`, which removes the
store's bookkeeping rather than just its bytes, is the only configuration where
the loop never once exceeds 75% of a core — and it is the only one that gets
close to the machine, at 2244% of 2400%.

### The shortfall, apportioned

`attribution.py` charges each interval's shortfall to exactly one class, with
precedence work-starved > loop-bound > disk-wait, because a machine with
nothing runnable is not waiting for a resource. For the three complete runs
(shortfall 128.2, 130.0, 147.3 core-seconds out of 646, 646, 670 available):

| | normal_rep1 | normal_rep2 | normal_rep3 |
|---|---|---|---|
| work-starved (`ready + in_flight` < 24) | 13% | 5% | 24% |
| loop-bound (loop ≥ 90% of a core) | 38% | 46% | 36% |
| disk-wait (a thread in D) | 3% | 0% | 0% |
| neither of the three | 46% | 49% | 40% |
| — D-thread bound, needing no classification | 0.8% | 1.7% | 1.3% |

One row is worth quoting against ourselves: `nocache_rep1`, at 2265.8%, has a
D-thread bound of 41% — but of a shortfall of only 22.6 core-seconds, because
0.50 threads in D out of 24 cores is 2% of the machine and 2% of the machine is
most of what that run is missing. The disk becomes visible exactly where there
is almost nothing left to explain, which is the opposite of a bottleneck.

The 40–49% "neither" is mostly the loop between 50% and 90% of a core, which
the 90% threshold excludes and the dose-response table above shows is already
costing 300–800 percentage points of process CPU. That is why the band table,
not the threshold, is the result: no threshold has to be defended.

## Answers to the three questions asked

* **(i) Device I/O wait: 0.8–1.7% of the shortfall in the complete runs**, bounded directly by
  threads in D state, and independently by a device that is idle 90–95% of the
  time. `--no-write-cache` does not recover the missing CPU — it *loses*
  95 points of it while writing nothing at all. The disk hypothesis is dead.
* **(ii) The event loop being single-threaded: 36–46% by the ≥90% threshold,
  66–82% by the ≥75% band**, and the mechanism is confirmed rather than merely
  correlated by `--no-cache`, where the loop's total CPU drops 8× (17.2 → 2.1
  CPU-s) and the process gains 349 points (1895% → 2244%).
* **(iii) Neither: 5–24%** is work-starvation — the engine genuinely had fewer
  runnable nodes than the machine has cores, which is a property of this
  program's dependency graph (20 cases narrowing to one reduction) and matches
  the earlier finding that the missing CPU is in the tail. What remains after
  the loop and starvation is small and has no signature in these columns.

## Measured versus inferred

Measured: every CPU, wall, byte, io_tick and thread-state figure above, and the
band tables. Inferred: that the loop's CPU *causes* the workers' idleness
rather than accompanying it. The evidence for the causal direction is the
`--no-cache` control — an 8× drop in loop CPU with a 349-point rise in process
CPU, at a device write volume of zero in both the before and after — plus the
mechanism being serial by construction (dispatch, completion and eviction
bookkeeping all run on that one thread). Not measured: which of those three
duties dominates the loop's 17 CPU-seconds. That is the next experiment, and it
needs the loop's own work broken out, not more resource counters.

One number here is unexplained and is left as an open question rather than
tidied away: under `--sparse-cache` the process is charged 3.90 GB of
`write_bytes` while only 0.10 GB of sectors reach the device and
`cancelled_write_bytes` stays at 0. `normal` shows no such gap (5.71 GB against
4.17 GB). Whatever that is, it does not touch the conclusion: both counters
agree that `--no-write-cache` writes nothing, and it still loses CPU.

## The blocker

Three of the four configurations cannot finish this program at all: `13/16`
goals on 9 of 9 runs, with `--no-cache`, `--no-write-cache` and
`--sparse-cache` alike. This is **an engine defect, not a property of the
program**, and the measurement files record it in the same file as the
timings, which is why it was not mistaken for a fast run — three of them look
faster than `normal`, and `nocache` looks like the best result in the table.

The engine's own error, from the `outcome` block of the measurement files:

* `--no-cache`: `NodeExecutionError: default.sequence failed while evaluating
  node de1ac008e1b8`
* `--no-write-cache`, `--sparse-cache`: no error at all, and 13 goals resolved
  — the engine drained with three goals still unresolved.

The cause is that the store is load-bearing for correctness, not just for
speed: `ComputationEngine._rematerialize` recovers a value evicted under memory
pressure by reloading it from the store, and when there is nothing to reload,
a node whose value comes from expansion (a `for` loop's spliced sequence) has
no road back. Two candidate fixes were tried on the host and both only moved
the failure, so both were reverted rather than committed:

1. Keeping the loop→sequence alias instead of popping it in `_worker` (the
   pop discards the only copy of the mapping `_resolve_reference` needs).
   Result: `NeedsExpansion` became
   `KeyError: 903d965f…` from `executor._compute`.
2. Additionally recording the alias-resolved value under the node's own id via
   `table.set_value`, as the disk-reload path does. Result: still 13/16 in the
   engine, and the post-run print path then ground for over two minutes
   recomputing subtrees.

The real fix belongs with whoever owns the eviction/expansion interaction: the
three unresolved goals are decided inside `engine.run()`, before any printing,
so it is a scheduling failure and not a materialisation one.
