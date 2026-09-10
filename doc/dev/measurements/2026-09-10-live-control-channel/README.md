# Why the engine now serves its own inspector

**Target** — the sixty-case oracle sweep,
`looping_experiment/brats027_oracle60.imgql`, on the 24-core reference host
(Intel Ultra 9 285K, 61.2 GB RAM, free-threaded CPython 3.14t, `-X gil=0`),
warm store, `--threads 32 --sparse-cache --cache-max-gb 250`.

**Method** — this note is the *record of a failure of method*, and the artefact
it produced. Nothing here was measured by the instrument that should have
measured it, because the run was launched without `--measure-series` and the
report is written only at exit. Everything below therefore comes from outside
the process, which is precisely the method `engine/measure.py` exists to
replace. Each number is reported with what it is worth.

## What was asked, and what answering it actually cost

The question was one sentence: *the cache did not survive, apparently.* Nine
external probes later:

| probe | what it gave | what it cost |
|---|---|---|
| `ls`/`du` on the payload directory | size, file count | two runs raced the evictor and filled 148 KB of output with `No such file or directory` |
| `sqlite3` on the store | unavailable — not installed on the host | — |
| copy of `.db`/`-wal`/`-shm`, then read | row counts, timestamps | 2.8 GB copied per look |
| `/proc/<pid>/io` | 248 GB written, 8.3 GB read in 26 min | first attempt read the `ptyrun` parent and got all zeros |
| per-thread `/proc/<pid>/task/*/stat` walk | the persister threads are hot | 136 files per sample — the exact cost rule 3 of `measure.py` forbids |
| `tr '\r' '\n'` over the tqdm log | the rate history | — |

And the follow-up — *turn persistence off and see* — could not be answered at
all, because the only way to change a parameter was to relaunch, and a relaunch
discards the graph state the question was about. That is the whole case for the
control channel: not convenience, but that a 26-minute-old run is a
non-reproducible experimental condition, and ending it to ask about it destroys
the thing being asked about.

## The findings, and what each is worth

**1. The store survived intact.** 2,662,360 rows, 1,670,430 payload files,
0 missing in a 4,000-row sample of `payload_file`. The consolidation of the
per-relaunch stores lost nothing.

**2. It is nonetheless nearly useless, and coverage is why.** Intersecting the
static plan's node ids with the store's materialized ids: **22,170 of 107,728**
(20.6%). The store was written by runs that all died inside goal 2 of 7, so it
holds the first two goals' subgraphs and nothing else. "The cache did not
survive" was the right observation and the wrong mechanism: nothing was lost,
there was never much there.

**3. The persister is the single largest non-kernel consumer.** A 10 s
per-thread sample: 136 threads, 1,625% total CPU, of which four
`voxlogica-persister` threads at 47.7–52.2% each ≈ **200%**, and the event-loop
thread at only **76.2%** — under one core. So the loop is *not* the current
bound; writing the store is the largest identified cost, against 20.6% reuse.
Worth: one 10 s sample, external, cost-unbounded. It is a direction, not a
number, and it is the first thing the new channel should be pointed at.

**4. The disk tier is at its cap and rewriting itself.** 264.6 GB of payloads
against `--cache-max-gb 250`; 248 GB written in 26 minutes against 8.3 GB read;
the payload directory *shrank* 261.4 → 253.3 GB over a 63 s window while the
process kept writing. Evict, recompute, rewrite. Two of three consecutive row
counts are consistent with roughly half of all persists being upserts of ids
the store already held.

**5. Throughput is drifting, not collapsing.** From the log, 26 minutes:
485 → 518 → 492 → 477 → 450 → 457 → 417 → 476 → 580 → 513 → 350 → 441 → 471 →
548 → 469 → 489 → 378 → 447 → **443** node/s. A ~9% drift inside a 350–580 band.
This is NOT the earlier 568 → 92 collapse (that was the unbounded spill queue,
fixed in `d364ff4`). An instantaneous 391 read off the progress bar is a dip,
not a trend — which is exactly the mistake a series file prevents and a progress
bar invites.

## Two retractions

**The `accessed_at` argument was void.** The store's `accessed_at` histogram
matched `created_at` almost row for row, and the obvious reading — "not one old
row was read by this run" — is wrong: `accessed_at` is written **only on
insert/upsert** (`storage.py`, the `ON CONFLICT` clause), never on read. The
store has *no read tracking at all*.

That is worth more than the argument it killed: **the disk-cache pruning work
(issue #67) cannot use recency, because recency is not recorded.** Any pruning
"a regola d'arte" must therefore be reachability-from-goals plus cost, or it
must first add read accounting — and adding it puts a write on the read path,
which needs measuring before it is built.

**The ENOSPC alarm was NOT wrong. This retraction is itself retracted.** Free
space was falling ~7 GB/min and the volume was 80% full, and I recorded here
that it could not matter because `_effective_max_bytes` re-derives the ceiling
from *current* free space on every probe interval. That is true and it is beside
the point: the re-derivation bounds the BUDGET, and nothing bounded the tier's
growth against the budget.

The next cold run, launched with `--cache-max-gb 300`, wrote **690.5 GB** in
1 h 54 m -- 709,248 payload files, 2.3x the budget -- and took the shared volume
from 735 GB free to 42 GB. It died with an empty log, because there was no space
left to write the failure into. Cause and fix in `0dda880`: `_enforce_budget`
gave up after a single 128-row pass whenever every candidate was live, which
under `--sparse-cache` is always.

The lesson is about the retraction, not the cap. I withdrew a correct alarm by
reading a mechanism (a budget that tracks free space) and inferring an outcome
(a tier that stays inside it) without measuring the outcome. `du` on the payload
directory would have settled it in one second, and I had already run `du` twice
that day.

## The artefact: `--control`

`implementation/python/voxlogica/engine/control.py` — a unix socket serving
newline-delimited JSON `Domain.method` requests, modelled on Chrome's DevTools
Protocol for its three useful decisions (id-keyed request/response, namespaced
methods, and *describe yourself* as a first-class method) and deliberately
without its fourth (no events, no subscriptions — a push channel would make the
instrument's cost depend on who is watching).

    Runtime.describe   every knob and probe this build has, with its doc
    Probe.get          the same snapshot the report records, live
    Knob.list/get/set  read and change a tunable, now
    Series.start/stop  turn the raw series on mid-run
    Measure.write      write a full report WITHOUT ending the run
    Runtime.eval       gated behind --control-eval, and stamped into the report

Knobs registered so far, each of which was a relaunch until today:
`governor.rss_share` (the 0.45/0.60 cliff), `store.cache_max_gb` (finding 4),
`persist.enabled` and `persist.min_compute_ms` (finding 3),
`engine.loop_delay_ns` (the causality probe that proved the loop bounds the
export tail).

Cost, measured on a 220 s smoke run with the sampler at 0.25 s:
`sampler_share_of_run_cpu` **0.000818**, and each control request reports its own
service time in its reply (`_served_us`: 20–33 µs for a knob or a queue probe,
4.4 ms to write a whole report mid-run). While no client is connected the
channel is one daemon thread blocked in `accept()`.

**A run whose parameters moved while it ran is not comparable with one whose did
not**, so every accepted `Knob.set` and every `eval` is stamped with its
wall-clock time into a `control` section of the report, with
`altered_while_running` as the flag the comparison tooling reads. That is the
same rule as `outcome`: a number that looks quotable must carry what would
disqualify it.

Client: `tools/measure/ctl.py` (dependency-free, works over a bare ssh).

```bash
ctl.py /path/run.ctl describe
ctl.py /path/run.ctl probe engine.queues
ctl.py /path/run.ctl set persist.enabled false
ctl.py /path/run.ctl watch probes.engine.queues.spill_pending 5
ctl.py /path/run.ctl report /tmp/now.json
```

## The next measurement, which this exists to make possible

Inside one run, on a fixed graph state: read `store.stats` and the throughput,
set `persist.enabled false`, read both again after five minutes, then set it
back. The prediction from finding 3 is a throughput rise of roughly the 200%
the persister threads hold; the thing that would refute the change being worth
anything is `recomputes`, which must be read in the same breath, because a
value with no disk copy cannot be evicted — it must be recomputed. Then the
same protocol on `persist.min_compute_ms`, which is the middle ground.
