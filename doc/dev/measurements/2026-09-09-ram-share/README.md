# The main reason: the engine is allowed 75% of RAM, and pays for it in direct reclaim

**+74% throughput from changing one constant.**

**Target.** Why the cores are not full on the REAL workload — the 60-case double
oracle sweep, not the 20-case calibration program. On an idle machine that sweep
was measured at ~1300% of 2400% while holding 42 GB of a 61 GB box.

**What was observed first.** With the sweep 30 minutes in: RSS 40–42 GB,
MemAvailable 10.8 GB, swap full but static, PSI memory `full` 2.7–3.8%, and
**`pgscan_direct` at 6,000–15,000 pages/s, continuously**. Direct reclaim is
page scanning performed by the thread that is trying to allocate — so a kernel
asking for a 9–35 MB output buffer does the kernel's memory housekeeping itself,
on a worker thread, instead of computing. Meanwhile htop showed every one of the
24 cores at 69–96%: no core idle, all partially busy, which is the signature of
a shared resource rather than of starvation.

**The policy.** `engine/governor.py`: `_RSS_SHARE = 0.75`. The governor is
allowed to let this process occupy 75% of the machine — 45.7 GB of 61 — and it
does. It is behaving exactly as configured.

**Method.** Two six-minute windows of the same program from the same warm store,
on an idle machine (other users 5–7%), differing only in that constant.
Throughput is node/s and mean process CPU over the window, sampled every 4 s.
These are windows, not complete runs, so wall time and goals are not the metric
here — the question is a mechanism, and the rate answers it.

## Result

| RAM share | mean CPU | mean RSS | **node/s** | nodes done in the window |
|---|---|---|---|---|
| **0.75** (as shipped) | 1986% | 30.4 GB | **304** | 63,194 |
| **0.40** | **2208%** | **19.6 GB** | **529** | **107,189** |

- **Throughput +74%.** 529 node/s against 304, and 107k nodes done in the window
  against 63k.
- **CPU +11%**, to 2208% — 22 of 24 cores.
- **RSS −35%**, 30.4 GB against 19.6 GB.

Holding less memory makes the engine substantially faster, because the memory it
holds is bought with the worker threads' own time.

## Why this is the main reason and the others were not

Everything measured before this is small beside it. The alias fix, which was a
real improvement, is worth 11% on the calibration sweep. The event loop, proven
causal by injection, bounds the export tail of that sweep. Both are true and
both are an order of magnitude smaller than 74%.

It also explains a discrepancy that had been sitting in the notes: the 20-case
calibration sweep reaches only ~12 GB of RSS, so it never approaches the
ceiling, and every measurement taken on it therefore *could not see this at
all*. The degradation is progressive — early in the big sweep, at 30 GB, CPU is
still 1986%; thirty minutes in, at 42 GB, it is 1300%. Measuring a small program
and generalising to the big one was the mistake underneath several days of work.

## What to change, and what still has to be measured

The constant is not the fix; the policy is. A fixed fraction of TOTAL memory
ignores two things the measurement makes visible: the kernel needs headroom for
page cache and reclaim, and the cost of not leaving it is charged to the
allocating thread rather than to the engine's own accounting. A governor that
targeted *available* memory, and that treated `pgscan_direct` as the signal it
is over the line, would adapt instead of being told a number.

Still to measure, before changing anything shipped:

1. The shape between 0.30 and 0.60 — 0.40 is a probe, not an optimum.
2. Whether the gain survives to completion, since a smaller live budget means
   more spilling and more recomputes; the window shows the rate, not the total.
   `cpu_seconds`, `recomputes` and peak RSS must all be read on full runs.
3. Whether `pgscan_direct` per second is a usable control signal for the
   governor, which would make this self-tuning rather than a new constant.

The total node count differs between the two windows (239,277 against 215,209)
because dynamic expansion depends on what is resident; the rate comparison is
unaffected but the absolute node counts are not comparable.
