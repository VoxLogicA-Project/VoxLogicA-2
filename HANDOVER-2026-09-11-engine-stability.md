# Handover — engine stability and performance, 2026-09-11

**Not committed, and not to be committed.** Written for a fresh agent with no
memory of the sessions that produced it. `HANDOVER.md` already exists and is
tracked (a July document about something else), so this took a distinct name
rather than clobbering it.

**Branch:** `perf-saturation`, at `48e0618`, pushed.
**Deadline:** a TACAS submission in roughly one month. The claim that paper
needs is *crash-free*, backed by a measured number, not an impression.

---

## 0. Read this first: the two transcripts

Everything below is a summary of two very long sessions. When the summary is
not enough, the raw record is on disk:

```
/Users/vincenzo/.claude/projects/-Users-vincenzo-data-local-repos-VoxLogicA-2/a53141ae-b85a-4ebe-8996-68d9bc68f908.jsonl   (18 MB, 2026-09-09/10)
/Users/vincenzo/.claude/projects/-Users-vincenzo-data-local-repos-VoxLogicA-2/35a86521-08d5-4212-ae2e-e3559c84cd93.jsonl   (4.6 MB, 2026-09-10/11)
```

They are JSONL; one object per line, `.message.content` holds the text. Grep
them rather than reading them whole.

**Working tree:** `/Users/vincenzo/data/local/repos/vlx-loop` (a worktree; the
primary checkout is `.../VoxLogicA-2` and other people use it — do not work
there).
**All execution happens on `fmt-5000`,** deployed to `/tmp/vlx-loop`. Nothing
heavier than a syntax check runs on the Mac; the user has asked for this twice.

---

## 1. Where things stand

| | |
|---|---|
| unit tests | 1268 pass, 3 skipped |
| soak matrix, overnight | 429 runs, 402 clean (93.7%), **11 rounds, 0 clean rounds** |
| failing configuration | `brats-threshold-sweep-aiim.imgql` + **warm store**: 0/11 at 8 threads, 2/11 at 24, 4/11 at 32 |
| everything else | clean, every round |

The failure is **intermittent and thread-count sensitive**. That is the single
most important fact in this document: it is a race, and the shape of the
dependence on thread count is itself the clue (see §3).

Acceptance criterion, fixed so it cannot drift: **20 consecutive clean ROUNDS**,
a round being one full pass over every program × cache mode × thread count.
Currently 0/20.

---

## 2. What was fixed, and what each cost to find

Nineteen commits since `ac0cf0d`. Ten-plus of the defects are **one class**: *a
value that only an expansion can produce, reached by a path that cannot produce
it.* They surfaced as seven unrelated-looking symptoms. The list matters
because it shows the shape of the problem, not because you need to re-read the
diffs.

| what | how long it took to characterise |
|---|---|
| spill queue grew with completions (5.9 M dead ids, 6× throughput loss) | 5.5 h sweep |
| `admission.wake_jobs` mutated a dict under iteration | 12 s into a sweep |
| a refused `await_one` return ignored at two sites | 1 h 37 m sweep |
| diagnostics context computed and then discarded | — (the reason the above cost 1 h 37 m) |
| a dead worker leaked a completion unit, silently | two stalls, `outstanding=8` then `49` |
| a recompute dropped its own argument (`KeyError`) | 4 min |
| `NeedsExpansion` on a pool thread | 4 min 10 s |
| run declared finished while a goal was unresolvable | 27 s |
| `goals_resolved` counted node ids, not queries | — (reported a complete run as 6/7) |
| `--cache-max-gb` was advisory: 690 GB written against a 300 GB cap | filled a shared disk |
| a rewriter's miss treated as a node failure | **2 s** |
| **handle holds released while the value was still resident** | **one run**, via clause (H) |

The trend in that right-hand column is the real result of the two sessions. It
is the effect of two artefacts built along the way (§5).

### The one that is a correctness bug, not a crash

`DependencyGraph.release` evicted a value *unless the node was `protected`*
(goals are), then popped its handle registrations **unconditionally**. So a
goal's value stayed resident while `names_handles` began answering "no" about
it — and `_eager` then handed an **eager** kernel raw `Handle` objects. `Handle`
is a frozen dataclass with no `order=True`: `<` and arithmetic raise, but `==`,
`hash`, `in`, `len` and `str` all succeed. **A kernel using only those would
have computed a wrong answer silently.** No such instance was observed — every
observed one raised — but "not observed" is not "impossible", and closing it
properly needs the equivalence harness in §7.

Fixed by driving the release from the value actually leaving the table
(`NodeTable._on_dropped`). `--no-cache` on the AIIM sweep went from a
deterministic 13/16 to 16/16 in 24 s.

---

## 3. The remaining failure, and what I now believe causes it

### The evidence

- Warm store only. Cold `--no-cache`, `--sparse-cache`, `--no-write-cache` all
  pass 16/16 with zero invariant violations.
- Intermittent, and monotone in thread count: 0/11 at t=8, 2/11 at t=24,
  4/11 at t=32.
- It surfaces as `NeedsExpansion` on a `default.for_loop`, reached from
  `strategy._side_effect → _materialize → resolve_deep → _resolve_reference →
  _rematerialize`, sometimes three levels of recursion deep.

### The mechanism I believe explains it

`NodeTable.load` accepts a stored container only if
`_references_are_answerable(value)`, whose test is:

```python
for handle in iter_handles(value):
    ref = handle.node
    if ref in self.values or ref in self.nodes:   # <-- self.nodes GROWS
        continue
    return False
return True
```

`self.nodes` is **this run's interned specs, and it grows as the run
proceeds** — expansion interns loop bodies into it. So **whether a cache hit is
accepted depends on when you ask**:

- ask early → refs not yet interned → refuse → the loop expands → correct;
- ask late → some other path has interned them → accept → the expansion is
  skipped → and any element with no value now has no way to be produced.

**The acceptance criterion for a cache hit is time-dependent.** That is not a
bug that can be patched at the point of failure; it is why every one of my
patches was refuted. Each of them changed *when* things happen, so each moved
the failure instead of removing it. Thread count changes interning timing,
which is exactly the observed dependence.

I want to be clear that this is a **hypothesis with strong circumstantial
support, not a proven cause.** See §6 for how to prove or kill it in about ten
minutes.

### Two candidate fixes

**(A) Make expansion-produced containers never serve from the store.** The
write half is already committed (`_EXPANDED_OPERATORS` /
`_container_is_recoverable` in `core.py`): such rows are no longer created. The
read half would make `load` refuse them outright. The decision then becomes
constant — loops always expand — and the race disappears with the interaction.

- Cost: re-deriving element ids in Python on every warm run. **Not** recomputing
  the elements: each hits the store individually, which is what
  `_references_are_answerable`'s own docstring says the refusal buys.
- I tried the scheduling half of this (`_available` refusing to prune
  expansion-produced nodes) and **reverted it**, because it did not fix the
  warm failure and it retained the loop's sequence, leaking 20 values in
  `test_values_die_with_their_last_consumer`. **I now think that revert was
  premature**: I judged a memory cost on a 20-element toy program and never
  measured it on a real one. But note it also *did not fix the failure*, which
  is evidence the scheduling half is not the whole story — the `load` half may
  be the one that matters.

**(B) Make the acceptance criterion time-independent instead of refusing
outright.** Require every ref to be *recoverable* — resident, or with a store
row — rather than "interned in this run". Keeps warm reuse where it is
genuinely safe. Riskier: `persisted` also grows during a run, though
monotonically, so the property becomes stable-once-true rather than
constant.

**My recommendation is (A), then measure.** It is the one that removes the
interaction rather than reasoning about it, and a month is not long enough to
win an argument with a race. But do §6 first.

---

## 4. Things I tried that did not work — read these before repeating them

Each is also recorded as a comment at the site where it was tried, so the code
tells you too.

1. **Filtering expansion-produced rows out of `materialized_ids()`.** Looks like
   the obvious read-half fix. It is a regression: reporting the row is what
   keeps the subtree pruned so the *elements* are served.
   `test_engine_caching.test_warm_run_reuses_runtime_expanded_nodes` went from 0
   recomputed nodes to 8.
2. **Resolving non-resident goals in the drain check.** Resolving a goal is not a
   probe — it *materialises* it. Measured harmful once, then that measurement
   turned out to be void (contaminated by a shared default store), then
   re-measured as harmless. **Lesson: give every run its own store.** The soak
   harness does; my ad-hoc loops did not, and I drew a wrong conclusion from it.
3. **Deferring `release_held()` to the caller.** Fixed nothing and leaked for
   every caller that is not the strategy — 22 values and 2 retained sequences
   held for the process's life.
4. **`_available` refusing to prune expansion-produced nodes.** See §3(A).
5. **Four separate attempts to predict, at drain, what materialisation would
   need and precompute it.** All refuted. Predicting it requires doing it. What
   finally helped was doing the materialisation *inside* `run()` (`at_drain`),
   which fixed warm t=24 but left warm t=8 stalling.

And two errors of method, which are the ones worth internalising:

- **I reported "ACCEPTANCE MET" from my own reporter while a configuration was
  failing every time it ran.** It counted consecutive *runs*; a round is 45 runs
  and that configuration is 3 of them, so a 27-run streak accumulated between
  failures. Now counted in rounds. **Check what your metric actually measures
  before quoting it.**
- **I retracted a correct ENOSPC warning by reasoning about a mechanism instead
  of measuring it.** I read `_effective_max_bytes` and concluded the tier was
  self-limiting; a cold run then put 690 GB on a shared disk against a 300 GB
  cap. Commit `325a403` retracts the retraction.

---

## 5. The two instruments — use them, they are why the numbers fell

### `--verify` (`implementation/python/voxlogica/engine/verify.py`)

The scheduler's invariant, written down and checked at runtime. Four clauses:

- **(P)** a frontier node waits only for something being produced (in flight,
  queued, parked, expanding, aliased, or on disk);
- **(T)** the frontier is empty exactly when no run-completion units remain;
- **(V)** a settled goal resolves to the bottom without further scheduling;
- **(H)** no unresolved handle reaches a kernel that declared neither `lazy` nor
  `shallow`.

Off by default, one `is None` test on the completion path when off.
`--verify=report` prints and continues, `--verify=strict` raises.

Two properties took iteration and must not be undone:
- **(P) violations are confirmed before reporting.** The check samples a
  frontier the loop thread is mutating; a dependency held between `pop()` and
  `begin()` looks unproduced. 18 false positives on a healthy sweep before the
  confirmation rule.
- **Nothing is reported once `_first_error` is set.** A dying run strands its
  frontier by design; 442 consequential violations once buried the one line that
  named the cause.

Clause (H) found the correctness bug in §2 **in a single run**.

### The soak matrix (`tools/soak.sh`, `tools/soak_report.py`)

3 programs × 5 cache modes × 3 thread counts, `--verify` on, one row per run
into `/tmp/soak/ledger.tsv`, each run with **its own store**.

```bash
ssh fmt-5000 'VLX_ROOT=/tmp/vlx-loop nohup /tmp/vlx-loop/tools/soak.sh &'
ssh fmt-5000 '.../python /tmp/vlx-loop/tools/soak_report.py'
```

It found the 2-second reproduction that had been costing 84-minute sweeps.
**Run it after every change, and read rounds, not runs.**

### `--control` (`engine/control.py`, client `tools/measure/ctl.py`)

A unix socket serving `Domain.method` requests on a running engine:
`Runtime.describe`, `Probe.get`, `Knob.list/get/set`, `Series.start/stop`,
`Measure.write`, and gated `Runtime.eval`. Live knobs for
`governor.rss_share`, `store.cache_max_gb`, `persist.enabled`,
`persist.min_compute_ms`, `engine.loop_delay_ns`. Every `Knob.set` is stamped
into the report as `altered_while_running`.

It answered "eight run-completion units are held by nothing" in one call.

---

## 6. What to do first (about ten minutes, and it decides the rest)

**Prove or kill the §3 hypothesis before writing any fix.**

The claim is that `_references_are_answerable` returns a time-dependent answer.
Test it directly: run the warm second pass with a probe that records, for every
`load()` of an expansion-produced node, whether it was accepted and how many of
its refs were in `values` / in `nodes` / neither.

```bash
# on fmt-5000, two passes over ONE store, t=8 (fails 0/11) and t=32 (passes 4/11)
/tmp/warmtest.sh          # already on the host; two passes, both thread counts
```

If accepted-vs-refused differs between passes or between thread counts for the
same node id, the hypothesis holds and fix (A) is justified. If acceptance is
constant, the hypothesis is wrong and the race is elsewhere — most likely in
`_splice`'s `_available(seq_id)` check, which has the same time-dependence.

Then, if (A) is justified: apply it, run the soak matrix to at least 5 clean
rounds, **and measure peak RSS on the real sweep before and after**, because
the memory cost is the one real objection and it has never been measured on
anything bigger than a toy.

---

## 7. The equivalence harness — still owed, and it is what closes correctness

Not built. It is the only thing that can retire the silent-wrongness question
raised in §2.

Run the same program under `--no-cache`, `--sparse-cache`, `--no-write-cache`,
cold store, warm store, and at 8/24/32 threads, and assert the goal values are
**identical**. The values are content-addressed, so identity is the right test,
not tolerance. Any disagreement is a correctness bug and outranks everything
else in this document.

---

## 8. Performance: how 2400% was reached, and what is left

### The measuring apparatus

`--measure <report>.json` (`engine/measure.py`) writes one self-describing
report; `--measure-series` adds the per-sample rows. Four rules, each of which
exists because breaking it produced a published wrong number: the process
measures itself; every sample carries its own timestamp; per-sample cost is
bounded and constant; the instrument measures its own footprint.

**The authority for total CPU is `getrusage(RUSAGE_SELF)` read once at start
and once at exit.** Rates in the series are computed from consecutive sample
timestamps — dividing by the nominal period once overstated CPU by 39%.
`outcome` in the report says whether the run actually finished: a broken build
once turned in the fastest time of three by aborting at 13 of 16 goals.

The host is an Intel Ultra 9 285K: CPUs 0–7 are P-cores, 8–23 E-cores, measured
1.28× apart. **The machine's real ceiling is ≈2050% in P-core-equivalents, not
2400%** — keep that in mind when reading any percentage here.

### What actually moved the number

| change | effect |
|---|---|
| `_RSS_SHARE` 0.75 → **0.45** (`governor.py`) | **+74% throughput.** A cliff, not a slope: 0.30→463–543 node/s, 0.50→536, **0.60→238**, 0.75→263–304, with CPU moving only 2090%→2069% across it. Less RAM is faster. |
| spill queue drained every turn (`d364ff4`) | 5,912,075 → 118 entries; had cost 568 → 92 node/s over 5.5 h |
| alias-not-copy (`ddf40f4`) | −11% wall, −3.2% CPU-s, −11% recomputes, −7% peak RSS; loop occupancy 67% → 40.8% |
| squared distance in `dt2` (`d7ebd22`) | −4% wall, Dice identical to 16 digits |

Proven by intervention, not correlation: injecting +2.4 ms of work per
completion *on the loop thread* nearly doubled a 26.8 s run to 52.7 s with
identical kernels and flat CPU-seconds — about **5 s of wall per millisecond of
per-completion loop cost**. That knob is still there
(`engine.loop_delay_ns`, live via `--control`).

### The best measured saturation

From a run of 84 minutes and 2,324,411 completions, 20,150 timestamped
intervals:

| | |
|---|---|
| mean CPU (getrusage, exact) | **1917%** of 2400% |
| **median interval** | **2274%** |
| p90 / p99 / max | 2375% / 2395% / **2416%** |
| intervals ≥ 2300% | **43.7%** |
| intervals ≥ 2050% (the P-core-equivalent ceiling) | **70.0%** |
| event-loop thread | mean **49%**, at ≥95% of one core in 1.8% of intervals |

**So the engine does reach 2400%, and does not hold it.** The distribution is
bimodal — saturated or nearly idle, little in between — and the mean is eaten
by roughly 19% of intervals near idle.

**Caveat that must travel with those numbers:** that run ended at 4 of 8 goals.
They are a valid sample of mid-sweep behaviour, *not* a complete sweep, and the
export tail — where I would most expect serial behaviour — is unmeasured. **No
complete, uncontaminated performance measurement of the full sweep exists yet.**
Getting one is a deliverable.

### What is left to do on performance

1. **The bimodal tail.** The loop is no longer the bottleneck (49%), so the
   idle intervals are waiting on something else. `runnable_vs_cpu` in the report
   says that when 0–4 threads are runnable the process averages 758%, and those
   were 740 of the sampled intervals. Issue #65 is the same shape.
2. **The persister.** Four `voxlogica-persister` threads held **~200% of CPU**
   while the store's coverage of the static plan was **22,170 of 107,728 nodes**
   (20.6%), and the tier wrote 248 GB against 8.3 GB read in 26 minutes. Issue
   #72 has the protocol: with `--control`, flip `persist.enabled` off and on
   *inside one run* and read `recomputes` in the same breath, since a value with
   no disk copy must be recomputed. This is a **paired** experiment, which is
   the whole point of the control channel.
3. **A governor that reads `MemAvailable` with `pgscan_direct` per second** as
   the over-the-line signal, instead of a fixed fraction of total RAM chosen on
   one host. Issue #71.
4. **A complete, clean sweep measurement** — see the caveat above.

Do not re-litigate these without the instrument: CPU% on this workload is
*anti-correlated* with speed (the fastest ITK width runs at 1859%, the
second-slowest at 2030%). The verdict is **wall time on a fixed mixed task**,
with CPU-seconds as the energy check, and `goals` gating all of it.
`doc/dev/measurements/README.md` states the reading rules in order of
authority; follow them.

---

## 9. My doubts, stated plainly

- **I am not certain the §3 hypothesis is the cause.** It explains the
  intermittency, the thread-count dependence and the warm-only nature, and no
  other candidate I examined does. But I did not prove it, and I have been
  wrong four times on this same failure. §6 exists so you do not inherit my
  confidence, only my evidence.
- **I do not know the memory cost of fix (A) on a real program.** I reverted a
  version of it on a toy-program measurement, which was poor judgement.
- **I do not know whether any published number is affected by the handle bug.**
  Every observed instance crashed, but the silent surface is real. §7 is the
  only thing that settles it, and until it is done I would not defend the
  segmentation numbers as *verified* — only as *unrefuted*.
- **`core.py` grew by 835 lines today** (much of it commentary and the
  verify/control wiring). The instrumentation is 991 lines in two new files that
  can be deleted without affecting behaviour. I do not think the engine is
  spiralling, but I am the wrong person to judge that and a fresh reader should
  say so if it reads badly.
- **`at_drain` (in `48e0618`) fixed warm t=24 and left warm t=8 stalling.** It
  is net positive and tested, but it is not obviously the right shape, and if
  fix (A) removes the underlying interaction it may become unnecessary. Consider
  reverting it once (A) is in, and measure.

---

## 10. Working rules that were learned the hard way

- Never run tests or sweeps on the Mac. Everything on `fmt-5000`.
- Give every run its own store. A shared default store produced at least one
  wrong conclusion in these sessions.
- Never `pkill -f`; it has killed the ssh session. Kill by PID.
- Redirect output to files and read excerpts; do not paste raw output.
- Every result states target, inputs and method. A run that did not resolve
  every goal has no timings worth reading.
- Write the failing test first, and check that it fails on the parent commit.
  Every fix in §2 has one.
- When a fix is refuted, record the refutation at the site where it was tried.
  That habit is the only reason the same three fixes were not attempted twice.

---

## 11. Stall verification, 2026-09-11 10:00 (25 rounds, 933 runs)

Deployed code verified identical to `48e0618`: every `.py` under
`implementation/python` on `fmt-5000:/tmp/vlx-loop` hashes equal to the local
working tree. (`git log` in the deploy dir still says `ddf40f4` — its git
metadata is stale, the files are not. Do not trust that `git log`.)

Ledger at 933 runs / 25 rounds: clean 869 (93.1%), **0 clean rounds**, best
consecutive-clean run streak 35.

| configuration | clean / runs | signature |
|---|---|---|
| threshold sweep (`brats-threshold-sweep-aiim.imgql`), warm store, t=8 | **1/25** | `NeedsExpansion`, exit 70, goals 7/16, `--verify` violations **0** |
| same, warm store, t=24 | **3/25** | `NeedsExpansion`, exit 70, goals 9–13/16, violations 0 |
| same, warm store, t=32 | **8/25** | `NeedsExpansion`, exit 70, goals 9/16, violations 0 |
| same, `--no-cache`, t=24 | 25/26 | **SIGABRT** (exit 134) at 4 s — *not* the stall |
| every other configuration (33 of 36) | 26/26 | — |

Two conclusions, both new:

1. **The stall reproduces at all three thread counts, not only t=8.** The
   monotone-in-threads picture in §1 (0/11, 2/11, 4/11) holds in the same
   direction over 25 rounds but is *worse* at t=24 than that sample suggested
   (3/25). It is one failure mode, warm-store only, and the goal counts cluster
   at 7/16 (t=8) and 9/16 (t=24, t=32) — i.e. the run stops at a *reproducible*
   point per thread count, which is a stronger clue than "intermittent" implies.
2. **`--verify` reports zero violations on every one of these failures.** The
   four clauses (P)(T)(V)(H) do not cover whatever this is. The checker is not
   going to find this one; §6's probe is still the way in.

3. **A distinct, rarer defect exists that is not a stall:** one `--no-cache`
   t=24 run died with `double free or corruption (out)` inside
   `SimpleITK.ReadImage`, called from
   `voxlogica/primitives/simpleitk/runtime.py:72`, under free-threaded CPython
   (`-X gil=0`). 1 in 26 runs of that configuration. A native double-free in a
   reader called concurrently is a thread-safety bug in the primitive, not the
   scheduler, and it is the only observed **crash** (as opposed to stall) in the
   933 runs. It deserves its own issue.

### The cacheless double sweep, restarted 10:05

Soak matrix stopped (PIDs 911823, 1178885/6, 1179283/4, by PID) to free the
machine. `/tmp/soak` store debris removed; **ledger and failure logs kept**.

The ~1.1 TB of warmed stores under `looping_experiment/_scratch` were **not**
deleted despite permission to do so: `--no-cache` reads and writes no store, so
no old store can contaminate this run, and the deletion is irreversible.

Launcher `/tmp/o60nc.sh` (untracked), `brats027_oracle60.imgql`, 32 threads,
`--no-cache`, `--verify=report`, `--measure` + `--measure-series`, `--control`.
Log: `looping_experiment/_scratch/o60_nocache.log`. At 00:23 it was at 226
node/s on 107,722 nodes. **The number that matters is not the peak rate but
whether it HOLDS** — the 2026-09-09 run fell 568 → 92 node/s over 5.5 h.

---

## 12. The cacheless double sweep collapsed at t≈3100 s — diagnosis, 2026-09-11 11:10

**It is not the 2026-09-09 collapse returning.** `spill_pending` is 0 and stays
0. This is a different mechanism with the same symptom, and it is measured, not
inferred.

### The curve (from `/tmp/voxlogica-memlog-1181907.tsv`, one row per 5 min)

| elapsed | completed | node/s over the interval | `evicted_early` | RSS |
|---:|---:|---:|---:|---:|
| 303 s | 111,464 | 367 | 0 | 26.4 GB |
| 608 s | 287,506 | 578 | 0 | 25.6 GB |
| 1215 s | 637,988 | 575 | 0 | 27.3 GB |
| 1825 s | 990,613 | 565 | 0 | 27.6 GB |
| 2433 s | 1,340,802 | 585 | 313 | 26.3 GB |
| 3042 s | 1,683,629 | 554 | 1,058 | 25.9 GB |
| **3356 s** | 1,692,273 | **27.5** | **22,476** | 27.4 GB |
| 3800 s (live) | 1,694,006 | **3.7** | **53,914** | 26.4 GB |

Throughput held flat at ~570 node/s for fifty minutes and then fell off a cliff
inside one five-minute window. `evicted_early` rose by a factor of twenty in the
same window. The two are the same event.

### What is actually happening now (two control-channel samples, 100 s apart)

| counter | delta over 100 s | rate |
|---|---:|---:|
| `completed` | +373 | 3.7 /s |
| `evicted_early` | +6,848 | **68 /s** |
| `recomputes` | +6,898 | **69 /s** |
| `kernels_executed` | +371 | 3.7 /s |
| `pgscan_direct` (kernel) | **+0** | 0 |

**The engine evicts and recomputes 18.5 values for every one node of forward
progress.** Evictions and recomputes match one-for-one, to within 1%. CPU is
409% (from `/proc/<pid>/stat` utime+stime over the same window) against ~2050%
during the healthy phase; `top -H` shows 3 of 396 threads running.

`pgscan_direct` did not move, so this is **not** OS memory thrashing and not
swap: the box has 27.5 GB available and the 8 GB of swap in use is static. The
thrash is entirely inside the engine's own value table.

### The mechanism

`--no-cache` means *no store is read and none is written*. The census says it
outright: **`durable: 0`**, `undurable: 17.8 GB`. Nothing the engine holds has a
disk copy.

The governor is at its ceiling and trimming (`gov_pressure` 0.956,
`gov_trims` 719, budget 20.5 GB against a 28.2 GB ceiling). Its eviction policy
is written for a world where an evicted value can be read back from the store in
milliseconds. With `--no-cache` there is nothing to read back, so **every
eviction converts directly into a full recompute**, the recompute allocates,
the allocation forces the next eviction, and the loop closes. It is a livelock,
not a stall — the run is busy, it is simply doing 18.5 units of destroy-and-redo
for each unit of progress.

Two further facts make it worse rather than better:

- **15.9 GB of the 17.8 GB live set is two operators** — `vox1.percentiles`
  (8.07 GB resident, 238 executions, 342 ms each) and `vox1.intensity` (8.04 GB,
  360 executions, 97 ms each). They are retained because they still have
  consumers, which leaves roughly 2–4 GB of working room for everything else. So
  the churn happens in a very small window against a very large floor.
- The eviction policy is **cost-blind**: it holds 16 GB of values that cost
  ~100–350 ms each to rebuild while it churns values elsewhere, and the kernel
  reported as `executing` throughout the collapse is `nnunet.predict`, which
  is serialised by a per-predictor lock (`primitives/nnunet/predictor_registry.py`,
  `lock_for`) and is the most expensive kernel in the program (60 executions,
  2,384 s of kernel time, ~40 s each).

### What this is and is not

- **Not** a regression from the nineteen commits: the healthy phase ran at
  570 node/s for fifty minutes on exactly this code.
- **Not** the spill-queue defect of 2026-09-09 (`spill_pending` = 0 throughout).
- **Not** the warm-store `NeedsExpansion` stall of §11 (no error, no violation,
  the run is progressing — just 150× too slowly).
- **It is an engine policy defect**, and a real one for the paper: the governor
  has no rule that says *an eviction whose value has no durable copy is a
  recompute, and a recompute I will immediately need again is worse than
  admitting less work*. With a disk tier that rule does not matter much. With
  `--no-cache` it is the whole game.

### Consequence for the run in progress

103,000 nodes remain. At 3.7 node/s that is ~7.7 hours **and falling** — the
progress bar's own ETA (12 minutes) is computed from the healthy-phase average
and is wrong by two orders of magnitude.

### The correction for the next measurement

**"No cache" is not the same as "cold cache", and the clean-measurement property
the user wants is the second one.** A *cold* store — `--sparse-cache
--store-db <fresh path>` — is equally uncontaminated (nothing in it at t=0,
nothing from the 1.1 TB of old stores can reach it) and it restores the
assumption the eviction policy is built on, so a trim costs a disk write instead
of a recompute. That is the run to make, and the one whose numbers mean
something.

---

## 13. Clean slate and the cold-store relaunch, 2026-09-11 12:36

**Every VoxLogicA store on `fmt-5000` was deleted** (user-authorised): 358 paths
matching `*.db`, `*.db.files`, `*.db-wal`, `*.db-shm` under
`/home/vincenzo/data/local/repos/VoxLogicA-2`, `/tmp`, and the default store at
`~/.voxlogica/results.db`. The list is `/tmp/purge.list` on the host; the script
is `/tmp/purge.sh`. **1.2 TB freed** — the root filesystem went from 84% to 51%
used (578 GB → 1.7 TB available). Programs (`.imgql`), logs, measurement
reports and `~/.voxlogica/diagnostics` were **not** touched.

The largest were `sweep_v3.db.files` (472 GB), `oracle60.db.files` (239 GB),
`b23_handles.db.files` (160 GB), `trainprobe.db.files` (138 GB) and
`all369_v2.db.files` (93 GB) — all from experiments of August and early
September.

The `--no-cache` run (PID 1181907) was killed at 1:10:00 elapsed, 1,694,006 of
1,796,886 nodes, 2 of 7 goals, while running at 3.7 node/s. Its log and report
are kept at `_scratch/o60_nocache.*` as the evidence for §12.

**Relaunched cold:** `/tmp/o60cold2.sh` — `brats027_oracle60.imgql`, 32 threads,
`--sparse-cache` on a store created empty at t=0
(`_scratch/o60_cold2.db`), `--cache-max-gb 300`, `--verify=report`,
`--measure` + `--measure-series`, `--control`. Log:
`_scratch/o60_cold2.log`.

**The comparison this run exists to make**, against §12's numbers on the same
program, the same code and the same machine:

| | `--no-cache` (§12) | cold store (this run) |
|---|---|---|
| healthy-phase throughput | 570 node/s for 50 min | to be measured |
| throughput after the governor starts trimming | **3.7 node/s** | to be measured |
| evictions per net completed node | **18.5** | to be measured |
| `durable` bytes | **0** | should grow |

If the cold store holds its rate through the trimming phase, §12's mechanism is
confirmed by intervention and the fix for the *measurement* is simply "never
measure this workload with `--no-cache`". If it collapses the same way, the
eviction policy itself is the defect and the store is not what saves it.

---

## 14. The `[verify:periodic] [P]` flood is the checker, not the engine

The cold run prints ~455 confirmed (P) violations per periodic check from about
2:36 elapsed onwards. **The run is healthy while it does so** — 531 node/s at
6:27, goals resolving, no error. The evidence that these are false positives:

| observation | `--no-cache` run | cold run |
|---|---:|---:|
| confirmed (P) per check | 192–231 | 451–460 |
| checks so far | 170 | 6 |
| **printed frontier ids, distinct** | **1,360 of 1,360** | 96 of 96 |
| throughput while reporting | 570 node/s | 531 node/s |

Two facts kill the "stranded set" reading:

1. **The count is stable, not growing.** A genuine leak accumulates; this sits
   at a steady-state population for over an hour.
2. **Zero repeats in 1,360 printed ids across 170 checks.** Eight of ~200 are
   printed per check. If the confirmed set were a fixed pool of stranded nodes,
   the same ids would be printed again and again. The population is stable in
   *size* and completely different in *membership* every time — a rotating
   window, which is what work-in-progress looks like and what a leak does not.

The count also scales with throughput and concurrency (455 at 531 node/s against
~210 at 570 node/s on a different cache mode), which is the signature of a
sampling artefact rather than a defect.

### Why the confirmation rule does not catch them (hypothesis, not proven)

`_producer_of` asks whether an unmet dependency is *itself* already available,
running, queued, job-owned, aliased or on disk. It has **no case for "the
dependency is a frontier node whose own dependencies are in production"** — the
property is transitive and the test is not. A dependency two or three levels
down a chain that is being worked on legitimately has no producer by that
definition, and it stays that way for longer than one check interval
(`self._every` completions, about a second at 500 node/s), so **phase-one
confirmation re-confirms it instead of filtering it**.

I tried to confirm this by intersecting the printed frontier ids with the
printed unmet-dependency ids: 32 of 1,360 overlap in one run, 0 of 96 in the
other. **That test has almost no power** — only 8 of ~455 violators are printed
per check, so the chance that both ends of a chain land in the same tiny sample
is negligible. It neither supports nor refutes the hypothesis; the proof would
be to have `_producer_of` return "dep is itself on the frontier with a producer"
and see the count fall to zero on a healthy run.

### What this means for the paper's claim

Clause (P) as written is **not sound at production scale**, and the soak ledger
counts `^[verify` lines as violations. Any long run would now be scored as
violating hundreds of times while being perfectly healthy. Before the matrix is
run again, either (P) is made transitive or the periodic (P) check is demoted to
a counter. `at_drain` is unaffected: it only runs (P) when the engine is
quiescent, which is when the non-transitive test is actually valid.

---

## 15. The disk tier's cap is the binding constraint — proved by intervention, 13:46

The cold run reached **300 GB of payload files at 1:09 elapsed — exactly
`--cache-max-gb 300`** — and the throughput fell from 512 to 124 node/s within
two minutes. `persist_shed` stood at 509,752: half a million values the tier
refused to write because it was at its bound.

I raised the knob live, `store.cache_max_gb` 300 → 800, over the control
channel. **This stamps `altered_while_running` into the report and the run's
timings are therefore not a clean published number** — the alternative was
losing seventy minutes of work to the §12 thrash, which would have produced no
number at all.

The paired result, same run, same code, ninety seconds apart:

| | at the 300 GB cap | 60 s after raising it to 800 GB |
|---|---:|---:|
| throughput | **124 node/s** | **465 node/s** (28k completions in 60 s) |
| evictions / s | 141 | **8.5** |
| recomputes / s | 145 | **8.5** |
| `gov_pressure` | 0.96 | 0.77 |
| `undurable` bytes | rising | 83 KB |

**This is §12's mechanism reproduced deliberately and then switched off.** A
value with no durable copy cannot be evicted cheaply, so the governor's trim
becomes a recompute; the tier at its cap and `--no-cache` are the same condition
reached two different ways. Seventeen times less churn and 3.7× the throughput,
from one knob, with nothing else changed. It is the cleanest causal evidence in
this document, and it is exactly the paired experiment the control channel was
built for (§5).

**Two things follow for the engine, not the measurement:**

1. `--cache-max-gb` reaching its bound should not be silent. `persist_shed`
   passed half a million with no warning; the operator's first sign was a 4×
   throughput loss. The engine knows the tier is full — it should say so.
2. The governor and the store do not talk. The governor keeps trimming at the
   same rate when the tier can no longer absorb what it trims, which converts
   every trim into a recompute. A governor that knew the tier was at its bound
   should stop trimming and throttle admission instead. This is the same missing
   rule as §12, seen from the other side.

### Machine status, 13:46

The box is ours: the only other load is another user's editor sessions at ~3% of
one core and under 1 GB total. Our process is at **1666% CPU**, 22.9 GB RSS, 397
threads. Memory 26 of 61 GB used, 34 GB available; **swap is 7 of 7 GB used and
has been static for hours** — it was consumed before this run and is not moving,
so it is not part of this story. Disk 2.0 TB of 3.6 TB used, 1.5 TB free, so the
800 GB cap leaves roughly 700 GB of headroom.

---

## 16. Correction to §15: the disk tier never threw a needed value away

**The user's objection is right and §15 got the mechanism wrong.** Disk eviction
on this workload IS nearly free, and it is not what cost the 4× throughput.
Measured from the store's own counters on the live run at 1:37 elapsed:

| counter | value |
|---|---:|
| `evicted_live` | **0** |
| `evicted_dead` | 35,933 |
| `tombstones` | 35,933 |
| `payload_entries` | 561,400 |
| `payload_bytes` | 601.7 GB |

Every row the tier evicted was **dead** — a value nothing live could still read —
and the count equals the tombstone count exactly. Not one durable copy of a
needed value was discarded. `_enforce_budget` orders candidates by
GreedyDual-Size, takes dead rows first and only touches live rows when no dead
row remains; on this run it never reached that branch.

**Two things in §15 are wrong and are corrected here:**

1. `persist_shed` (509,752 then, 762,568 now) is **not** the cap refusing
   writes. It is the persister's own pressure shedding — `shed_pressure` in
   `engine/persist.py:_shed`, governed by `persist.min_compute_ms` — which
   declines to write values cheaper than the threshold. It rises all run long,
   at the cap and away from it, and had nothing to do with the collapse.
2. The collapse at the cap was therefore **not** paid in lost durable copies.

### What being at the cap actually costs

What is measured, and is not in doubt:

| at the 300 GB cap | 60 s after raising to 800 GB |
|---:|---:|
| 124 node/s | 465 node/s |
| 141 RAM evictions/s | 8.5 /s |
| 145 recomputes/s | 8.5 /s |
| `gov_pressure` 0.96 | 0.77 |

Since `evicted_live` is 0, those 145 recomputes per second cannot be values the
disk dropped. They are **RAM evictions of values that had no disk row at all** —
the governor trimmed them and there was nothing to read back.

Why they had no disk row is the part I have **not** proved. The likely chain, and
it is a hypothesis: while over budget, `_enforce_budget` runs on the persister
thread and loops — `while self._payload_bytes > low_water`, re-running the
ordered `_EVICT_SCAN` over SQLite on each pass — **holding `self._lock` for the
whole loop**. A persister busy in that loop is not draining its write queue, so
completions pile up undurable in RAM, and the governor then trims exactly those.
The cost of the cap is the *enforcement*, not the eviction.

To prove or kill it: count the persister's queue depth and the time spent inside
`_enforce_budget` while over budget. Both are one counter each and neither
exists yet.

### The bound is coming back

The tier reached 601.7 GB at 1:37 and is growing at ~11 GB/min, so the 800 GiB
cap returns in about 25 minutes. Above it, `_effective_max_bytes` would bind
instead: with 1.2 TB free on a 3.6 TB volume the reserve is 180 GB (5% of total,
floor 50 GB) and the ceiling sits near 1.6 TB — roughly 95 minutes away. The
run is at 2 of 7 goals after 1:38, so **on the present trajectory this sweep
cannot finish inside any disk bound this machine has.** Either the tier stops
absorbing the cheap tail (`persist.min_compute_ms` well above its present 1.0 ms
— the knob exists for exactly this) or the run meets a cap again and pays the
same 4×.

---

## 17. The second, milder drop at 2:23 — a recompute tax, not a collapse

Throughput fell from ~570 to ~300–410 node/s. **This is a different and much
smaller thing than §12 or §15.** Measured over 83 s at 2:23 elapsed:

| | value |
|---|---:|
| completions | 24,498 → **295 /s** |
| RAM evictions (`evicted_early`) | 9,629 → **116 /s** |
| recomputes | 9,394 → **113 /s** |
| CPU (`utime+stime` over the window) | **1797%** |
| `gov_pressure` | 0.81 |
| `resident_values` | **5,870** (21,238 an hour ago) |
| `evicted_live` / `evicted_dead` | 0 / 61,692 (unchanged) |
| `payload_bytes` | 807 GB of an 859 GB budget (94%) |

The engine is **busy, not stalled**: 1797% CPU against ~2050% in the healthy
phase. What changed is the ratio of useful work to redo. **113 recomputes per
second against 295 completions per second — about 28% of everything the engine
does is work it has already done**, against 1.8% (8.5 against 465) an hour
earlier.

The disk tier is not the cause: it is under its budget and `evicted_live` is
still 0. The cause is RAM. The sweep's live set grows as more cases come into
flight, the governor trims harder (resident values down 3.6×), and **1,158,165
values have no disk copy to come back from** — `persist.min_compute_ms` is 1.0 ms
and `persist_shed` has been declining to write anything cheaper all run. A
trimmed value with no row is a recompute, by construction.

### The knob dilemma, stated honestly

The two goals are in direct opposition on this workload:

- **Stop the tier hitting its cap** → shed more (raise `persist.min_compute_ms`)
  → more values with no disk row → **more** recomputes.
- **Stop the recomputes** → shed less → tier grows faster → cap sooner.

My earlier recommendation to raise `persist.min_compute_ms` was the wrong half
of that trade for the situation we are actually in, and the measurement above is
what shows it: the recompute tax is already the live cost, and disk is the
resource we still have.

### Decision taken, 15:05

`store.cache_max_gb` 800 → **1400** over the control channel. Rationale, in
order:

1. The tier was at 807 of 859 GB and growing 6.6 GB/min — **about 8 minutes**
   from repeating §15's 4× loss.
2. The disk is protected without me: `_effective_max_bytes` re-probes free space
   every 5 s and keeps a reserve of 180 GB (5% of a 3.6 TB volume), so the real
   ceiling is ≈1.59 TB whatever the knob says. 1400 GB sits just under it and
   leaves the reserve intact. This is the mechanism that was missing when a
   cold sweep once put 690 GB on a shared disk (§4, commit `325a403`).
3. `persist.min_compute_ms` left at 1.0 ms, because the measurement says
   shedding is what creates the recomputes.

**This does not make the sweep fit.** At 2 of 7 goals after 2:23, with the plan
still expanding (107,728 static nodes → 3.86 M and rising) and the tier growing
6.6 GB/min, a complete run needs several terabytes. The honest position is that
**the full cold sweep does not fit on this machine with a full store**, and the
deliverable of §8 — "a complete, clean sweep measurement" — needs either a
smaller sweep, a store on a bigger volume, or an engine that sheds by *recompute
cost* rather than by a millisecond threshold.

---

## 18. Sizing the "unfold the whole DAG and refcount it exactly" proposal

Three answers: the knob is the wrong lever; the arcs cost roughly what you
guessed; and the arcs are not what would stop you.

### 1. `persist.min_compute_ms` — do not raise it

It decides which completions get a disk row. Raising it writes fewer, and a
value with no row that the governor trims is a **recompute**. §17 measured the
tax that creates: 113 recomputes/s against 295 completions/s. Raising the
threshold buys disk by making that number worse. Lowering it buys throughput by
making the tier grow faster. It is a dial between two costs, not a fix for
either, and the diagnosis it cannot touch is that eviction here is **blind to
what a value will be worth later**.

### 2. What exact global refcounting actually costs

Mean fan-in on this program is **≈1.4** — estimated by weighting the run's op
census (`kernel_by_op`) by each operator's arity; the unary image operators
(`dt`, `dt2`, `not`, `volume`, `border`) outnumber the binary ones roughly 3:2.
So E ≈ 1.4 N.

Packed, the way you would actually build it — CSR arrays, `int32` node indices:

| structure | bytes per node | 20 M | 100 M |
|---|---:|---:|---:|
| forward offsets + targets | 4 + 5.6 | 192 MB | 960 MB |
| backward offsets + targets | 4 + 5.6 | 192 MB | 960 MB |
| refcount (`int32`) | 4 | 80 MB | 400 MB |
| **arcs + refcounts** | **23.2** | **0.46 GB** | **2.3 GB** |
| + 32-byte digest per node | 32 | 0.64 GB | 3.2 GB |
| + hash→index table (70% load) | ~29 | 0.57 GB | 2.9 GB |
| **with identity** | **~84** | **1.7 GB** | **8.3 GB** |

**Your instinct is right: 1–3 GB**, for the arcs and refcounts, at 100 M nodes.
Identity is what triples it, and identity is only needed at that width if
hash-consing must stay content-addressed across the whole plan.

The same structures built the way this codebase builds things — `NodeId` is a
**64-character sha256 hex `str`**, arcs as Python `frozenset`/`list`, refcounts
as `dict[NodeId, int]` — cost 190–440 bytes per node, i.e. **4–9 GB at 20 M and
19–44 GB at 100 M**. The representation, not the idea, decides whether this is
affordable.

### 3. The cost that would actually stop you, measured

The arcs are the cheap part. Read off the live run at 2:39 elapsed:

| | |
|---|---:|
| nodes registered | 4,327,191 |
| RSS | 23.0 GB |
| accounted payload (`accounted_bytes`) | 15.1 GB |
| **everything else** | **7.9 GB** |
| **per registered node** | **≈1.8 kB** (≈1.5 kB after allowing ~1.5 GB of interpreter, SimpleITK and thread stacks) |

That 1.5 kB is already **plan-proportional, not frontier-proportional**: the
graph's docstring is explicit that a completed node still leaves "their (shared,
hash-consed) spec in `table.nodes`", and `NodeId` strings live as long as the
specs do. Extrapolated at the same per-node cost: **~30 GB at 20 M nodes,
~150 GB at 100 M** — before one arc or one refcount is added.

So the proposal is affordable if and only if the per-node record drops from
~1.5 kB to tens of bytes: specs interned into packed arrays, ids as 16-byte
binary keys rather than 64-character Python strings, arcs in CSR. That is the
project. The arcs and the exact refcounts — the part that was in question — are
a rounding error next to it.

### 4. What the proposal would actually buy, and it is the right prize

The engine **already** reference-counts exactly (`graph.consumers`: "each
registered consumer holds one reference to each of its inputs; the last release
evicts the value"). What it does not have is *global* counts, because that state
is deliberately frontier-scoped. The consequence is §12 and §17's defect: when
the governor must free memory it cannot ask **"how many future consumers does
this value have, and what will it cost to rebuild?"** It evicted 16 GB worth of
`percentiles` and `intensity` — 342 ms and 97 ms to rebuild — on the same
footing as an `nnunet.predict` output that costs ~40 s and is serialised behind
a lock.

A plan-wide backward arc set answers both questions exactly and turns eviction
from a guess into an ordering. **That is the fix for the cost-blindness, and no
amount of disk-cap or shedding-threshold tuning substitutes for it.**

---

## 19. `persist.min_compute_ms = 0` would not fix it, and would likely hurt

I had the mechanism wrong in §17 and §18 and the code says so plainly.
**`persist_shed` has nothing to do with `persist.min_compute_ms`.** They are two
different gates:

| gate | where | rule |
|---|---|---|
| `persist.min_compute_ms` | the event loop, `core.py:1154` | `critical or compute_ms >= persist_min_compute_ms` — a cheap value is never offered to the persister |
| **pressure shedding** (`persist_shed`) | the persister, `persist.py:_shed` | **"IF I WOULD HAVE TO QUEUE, DROP IT"** — if `queue.qsize() >= num_writers` and the value is rebuildable, it is not written. **There is no threshold and nothing to configure.** |

So the 1.66 M values with no disk row are **not** cheap values refused by a
millisecond threshold. They are values the writers could not absorb: four writer
threads (`min(4, cpu_count())`), each compressing, and the queue was full when
the value arrived.

Counted at 3:11 elapsed, out of 5,150,529 completions: **1,038,268 written**,
**1,659,062 pressure-shed**, 293,087 skipped as dead.

### Why 0 ms makes it worse rather than better

Setting the threshold to zero offers *more* values to a queue that is already
full. It cannot raise the write rate — that is bounded by four compressors and
the disk — so the extra arrivals are shed too. And `_shed` drops **whatever
arrives while the queue is busy**, with no regard for what the value cost to
produce: its only test is `probe(node_id)`, "could the engine rebuild this?",
never "at what price?". Diluting the arrival stream with cheap values therefore
makes an **expensive** value *less* likely to win a writer slot. The store would
hold more rows of less value.

### The third instance of the same defect

This is now the same blindness in three places, and it is the finding of the day:

| decision | asks | never asks |
|---|---|---|
| governor RAM trim (§12, §17) | how many bytes can I free | what will rebuilding cost |
| disk tier eviction (`_enforce_budget`) | GreedyDual-Size **per byte** — the only one that is cost-aware at all | — |
| persister shedding (`_shed`) | **can** this be rebuilt | at what price |

`_shed` has the recompute probe in its hand already. Giving it the compute cost
it is deciding about — shed the 20 ms `not`, never shed the 40 s
`nnunet.predict` — is a small change and is the one that would have prevented
most of §17's tax.

### The run is about to hit the wall for real

| at 3:11 elapsed | |
|---|---:|
| goals | 2 of 7 |
| nodes | 5,251,951 and rising |
| payload tier | **1.358 TB** of the 1.4 GiB-based cap (1.503 TB) — 90% |
| tier growth | **11.5 GB/min** |
| time to the cap | **~13 minutes** |
| disk free | ~410 GB; ceiling (payload+free−180 GB reserve) ≈ 1.59 TB, ~20 minutes |

There is no third cap to raise: the disk reserve is the backstop and raising past
it is what filled a shared disk in §4. When the tier stops growing, dead rows are
nearly exhausted (61,692 evicted, `evicted_live` still 0), so `_enforce_budget`
will begin evicting **live** rows, and every one of those is a recompute.

**Recommendation: stop the run.** It has produced §12, §15, §17 and §18 — four
measured findings — and what remains is a degraded tail, not a measurement. The
full cold sweep with a full store does not fit on this machine, and the next
experiment should be designed around that fact rather than discovering it again
three hours in.

### 19a. Cap removed, 3:15 — the disk's own reserve is now the bound

`store.cache_max_gb` 1400 → 3000 (3.22 TB), which **does not take effect**:
`effective_max_bytes` reports **1.644 TB**, the disk-derived ceiling
(`payload + free − 180 GB reserve`), re-probed every 5 s. That is the designed
backstop from commit `325a403` and it is the last one — there is no knob above
it, and the 180 GB reserve is exactly what stops this filling a shared volume.

At 1.402 TB of payload and 11.5 GB/min, the ceiling binds in **~21 minutes**.
The run will not die: `_enforce_budget` will start evicting rows, dead ones are
nearly exhausted (61,692 evicted, `evicted_live` still 0), so it will evict live
rows and pay §15's throughput cost. Expect a step down to a few hundred node/s,
not a stall.

---

## 20. "Evict the oldest" — already done, in a better form, and not where the cost is

Three things the code settles.

**1. Age is already in the key, together with cost.** `storage.py:596`:

```python
gd_key = self._gd_clock + (compute_ms / payload_bytes)
```

`_EVICT_SCAN` takes rows `ORDER BY gd_key ASC` — cheapest-to-recompute *per
byte* first — and the GreedyDual clock rises to each evicted key, so a row
written long ago carries a low clock term and sorts out early. That is LRU **plus**
recompute cost **plus** size. Pure age would be strictly less informed: it would
evict a 40-second `nnunet.predict` output ahead of a 20-millisecond `not` purely
for being older.

**2. "Old" is not "unneeded", and the engine knows the difference exactly.**
`liveness.py` defines

> live(n) ≡ n is on the frontier ∨ n's value has unrun consumers (refcount > 0)
> ∨ n is staged by an open loop ∨ n is a goal of an unsettled query

— reference counting, not a conservative guess, and evaluated per candidate in
O(1). A value's age says nothing about whether its consumer has run. On this
sweep the goals are whole-sweep aggregates, so values produced in the first
minute can legitimately have an unrun consumer three hours later. Evicting them
"because they are old" is exactly the recompute we are trying to avoid.

Note also that liveness is **a preference, never a correctness gate**: the
docstring is explicit that every value is regenerable from lineage. Nothing here
can break a computation. The only currency is time.

**3. The measurement says the ordering was never the problem.** Across three cap
events, `evicted_live` is **0** and `evicted_dead` is 61,692. At the 300 GB cap
the tier freed ~30 GB using dead rows alone. So the policy has never yet had to
make the hard choice the question is about — and §16 already established that
the 4× loss at the cap was not paid in evictions at all, but in the enforcement
loop holding the store lock while the persister stopped draining its queue.

*Correction to §19/§19a: I wrote that dead rows were "nearly exhausted". That is
not what the counter says — 61,692 is how many were evicted to reach low water,
not how many exist. There is no measurement of the dead population, and the
evidence so far is that dead rows have sufficed every time.*

### Where the instinct is right

The useful version of "evict the oldest" is **"evict what is provably
finished"**, and that needs what §18 describes: plan-wide backward arcs give the
exact moment a case's last consumer has run, so the tier could release a whole
case in one step instead of discovering deadness 128 rows at a time through
repeated `ORDER BY gd_key` scans under the store lock. Same instinct, exact
instead of heuristic, and it removes the §16 cost rather than reordering it.

---

## 21. Being at the bound is not what costs — measured at the ceiling, 4:37

The tier reached the 1.643 TB disk ceiling, enforced, and carried on. Over a
77 s window with the ceiling in force:

| | |
|---|---:|
| throughput | **562 node/s** — the best sustained rate of the run |
| recomputes | **27/s** (145/s at the 300 GB cap, 113/s at 2:23) |
| RAM evictions | 27/s, still 1:1 with recomputes |
| `evicted_dead` | 61,692 → **143,351** across the enforcement cycle |
| `evicted_live` | **0** |
| payload | 1.523 TB → 1.492 TB → 1.505 TB — freed ~45 GB, then grew again |

So the engine hit its hard bound, freed 45 GB from dead rows alone, and went
back to full speed. **The 4× loss at the 300 GB cap was not the cost of being at
a cap.**

### The difference, and it is a hypothesis

`_enforce_budget` scans `ORDER BY gd_key ASC LIMIT 128` and keeps only the rows
its liveness predicate calls dead. **The cost of a pass is set by how many of
those 128 are dead.** At 1:09 elapsed the run was young, few values had finished
their consumers, so a pass plausibly freed almost nothing and the loop re-scanned
— holding the store lock, starving the persister (§16). At 4:37, with 7.9 M
nodes completed, dead rows are plentiful and one pass suffices.

**Not measured:** the density of dead rows near the head of the `gd_key` order at
either moment. The counter that would settle it — rows scanned versus rows
evicted per enforcement pass — does not exist. It is one counter and it is worth
adding; it is the difference between "the cap is expensive" and "the cap is
expensive only while the graph is young".

### So: more disk is not the answer

1. It is not available — the ceiling is the disk's own, the volume is 92% full,
   and `store.cache_max_gb` is already set above it and doing nothing.
2. It would not buy what it looks like it buys: the bound is being hit right now
   at the best throughput of the run.
3. The durable fix is issue 74's: enforcement that cannot spin, and shedding and
   eviction ordered by recompute cost rather than by arrival or by size.

Still true and unchanged: at 2 of 7 goals after 4:37 with 8 M nodes registered,
**this sweep does not finish on this machine.** That is an experiment-design
problem, not a knob problem.

### 21a. The counterfactual: would a high ceiling from the start have avoided the slowdowns?

**One of the two, yes.** They had different causes and only one of them was the
cap.

| event | tier vs its budget at the time | cause | would a ceiling-high cap have prevented it? |
|---|---|---|---|
| 1:09, **512 → 124 node/s** | **at 300 GB of a 300 GB cap** | budget enforcement spinning on a young graph (§21) | **Yes.** Entirely self-inflicted by `--cache-max-gb 300`. Left at the disk ceiling the tier would not have reached a bound for ~2.5 h. |
| 2:23, **570 → 295 node/s**, 113 recomputes/s | 798 GB of an 859 GB budget — **93%, not enforcing** | the write path saturating: four writer threads, `persist_shed` running at ~236/s, so values had no disk row and the governor's RAM trim became a recompute | **No.** More disk headroom does not make four compressors faster. |

So the answer to "just raise the ceiling" is: **do set it at the disk ceiling
from the start** — that is issue 74's recommendation (B1), and it would have
removed the first and worst drop of the day — but it does not touch the second
class, which is write bandwidth and cost-blind shedding. For that the fix is
(B2): let `_shed` see the recompute cost it is deciding about.

---

## 22. 6:12 — out of memory and out of disk at the same time

Throughput 119 node/s (the bar reads 91), CPU **694%**. Measured over 85 s:

| | |
|---|---:|
| completions | **119 /s** |
| **recomputes** | **242 /s** — two units of redo per unit of progress |
| RAM evictions | 255 /s |
| payload tier | 1,556.5 GB of a 1,640.9 GB ceiling — **95%** |
| tier growth | **0.4 GB/min** (10.4 GB/min an hour ago) |
| `persist_shed` rate | 13 /s (236 /s an hour ago) |
| `evicted_live` | **27 — non-zero for the first time** |
| `evicted_bytes` (cumulative) | **2.42 TB** — more than the tier now holds |
| `resident_values` | 5,190 (12.1 GB live) |
| `gov_pressure` / `gov_sacrifice_ms` | 0.833 / **1.0** (149.6 earlier) |
| disk | 93% full, 263 GiB free |

### What it means

**Both tiers are full at once, and that is the whole story.**

- The **disk** tier is at 95% of a ceiling that tracks free space, so it has
  stopped absorbing: 0.4 GB/min against 10.4 an hour ago, and it has begun
  evicting *live* rows for the first time in the run.
- **RAM** holds 5,190 values. The governor trims 255/s.
- **3.93 M values were pressure-shed** over the run and have no disk row at all.

So a value trimmed from RAM has nowhere to come back from, and the trim becomes
a recompute — §12's mechanism exactly, now reached from the opposite direction:
there, no store existed; here, the store exists and is full.

`gov_sacrifice_ms` has fallen from 149.6 to 1.0, i.e. the governor is now
discarding only values it believes cost ~1 ms to rebuild. That 242 cheap
recomputes per second still cost 694% of CPU and 119 completions/s suggests the
recomputes **cascade** — rebuilding a cheap value needs inputs that were also
discarded, and each node of the cascade is counted. **Not proved:** there is no
counter for cascade depth, and it would be a useful one.

### This is the degraded tail

2 of 7 goals after 6:12, 11.1 M nodes registered, 2.42 TB already written and
deleted. The run is doing twice as much redo as progress and both tiers are
exhausted. **Nothing further can be learned from letting it continue**, and
everything it had to teach is in §12, §15, §16, §17, §19, §20 and §21.

---

## 23. Inspecting the store, and one live experiment: the cache IS full of garbage, and it is not the reason

Read-only SQLite queries against the running store, plus one forced garbage
collection over the control channel. Four findings, three of them measured
directly.

### 23.1 The tier is two operators

| kept (has payload) | rows | GB | avg MB | avg ms |
|---|---:|---:|---:|---:|
| `vox1.dt2` | 104,527 | **959.8** | 9.18 | 308 |
| `vox1.dt` | 63,202 | **551.8** | 8.73 | 319 |
| everything else (1.85 M rows) | 1,785,870 | 46.4 | 0.03 | ~60 |

**97% of 1.56 TB is two distance transforms.** The 1.85 M other rows — masks,
thresholds, conjunctions — are 3% of the bytes. GreedyDual-Size is behaving
exactly as designed: a 9 MB value costing 308 ms has a value density 40× lower
than a 30 kB value costing 42 ms, so the distance transforms go first. The
eviction *ordering* is not the defect.

### 23.2 There IS a large unneeded population, and it sits there by design

Forced a collection by lowering `store.cache_max_gb` 3000 → 1300 over the
control channel. **In 34 seconds:**

| | before | after |
|---|---:|---:|
| payload | 1,559.2 GB | **1,256.4 GB** (−302.8 GB) |
| `evicted_dead` | 244,374 | 275,757 (**+31,383**) |
| `evicted_live` | 27 | **27 — not one** |
| disk | 93% full | **85% full** (263 → 541 GiB free) |

**19% of the tier was dead**, at ~9.65 MB a row, and had been retained purely
because `_enforce_budget` returns immediately unless `payload > budget`. The
tier has no idle collection: garbage accumulates up to whatever the budget is,
and a bigger budget means more garbage, not more useful cache. *The user's
"I don't buy we need all the cache" was right.*

### 23.3 But it is not why the run slowed down

Throughput and redo over the 172 s after the collection: **116 node/s and 247
recomputes/s** — unchanged from before it. Freeing 303 GB of disk changed
nothing, because **the pressure is in RAM**: the governor trims 255/s and the
trimmed values have no disk row (3.93 M pressure-shed over the run), so each
trim is a recompute. Disk space was never the binding resource for throughput;
it was only ever the binding resource for *not dying*.

### 23.4 The visiting order is the real conditioning factor

The lifetime of a dead distance transform — time from creation to the moment its
last consumer released it:

| lifetime | rows | GB |
|---|---:|---:|
| < 1 min | 40 | 0.5 |
| 1–10 min | 2,275 | 26.5 |
| 10–60 min | 65,719 | 699.9 |
| **1–3 hours** | **166,942** | **1,647.3** |
| > 3 hours | 39,129 | 345.6 |

**A 9 MB value produced now is consumed for the last time one to three hours
later.** Only 2,315 of 274,105 died within ten minutes. The survivors look the
same: 66,295 rows (586 GB) are 1–3 h old and 20,116 (144 GB) over 3 h, still
waiting.

That is the whole memory problem in one table. The engine is not holding
terabytes because the data is big; it is holding terabytes because it produces a
value hours before it consumes it. A traversal that finished each case's
consumers before opening the next would keep the same computation inside a few
GB. **This is a scheduling-order defect, not a cache-sizing one, and no knob in
the control channel can reach it.**

*Caveat: the counterfactual is not proved. I have not demonstrated that a
different traversal order would keep throughput — only that the current one
holds values 1–3 hours, and that this is what fills the tier.*

### 23.5 A recomputed value never gets its disk row back

Of 2,735,741 rows, **2,459,957 have never been updated after creation**, and the
275,784 that were updated are exactly the evicted ones (275,757 dead + 27 live) —
the update is the tombstone, not a rewrite. So **no value has ever been
recomputed and re-stored.** Once a row is evicted or a value is shed, that node
is recomputed on every subsequent demand, forever. That is a waste amplifier and
it is probably cheap to fix.

### 23.6 The store keeps no lineage

`dependencies_json` and `expression_json` are `{}` for all 2,703,720 rows. The
comments in `liveness.py` and `_enforce_budget` say values are "regenerable from
lineage"; the lineage is in the running process's plan, not in the store. Two
consequences: nothing outside the process can say what a stored value is or how
to rebuild it, and the columns are dead weight in every row. Worth either
populating or dropping.

### Left in place

`store.cache_max_gb` is **left at 1300 GB**, not restored to 3000. It cost
nothing (no live eviction, no throughput change), it collects garbage that would
otherwise accumulate, and it holds the disk at 85% instead of 93%.

---

## 24. The run ended at 7:07:44 — not a crash, a self-detected scheduling failure

**No segfault, no OOM, no ENOSPC.** The engine reached drain, checked itself,
found a goal it could not resolve and refused to report success:

```
RuntimeError: engine finished with an unresolved goal;
this is a scheduling failure, not a successful empty result
```

`[verify:drain] [T] the run is complete by its own accounting (outstanding=0)
but 492 node(s) are still on the frontier`

Error id **VLX-8D9B9990**. Artefacts preserved next to the run:
`o60_cold2.report.json`, `o60_cold2.error.txt`, `o60_cold2.log` (23 MB), and
`o60_cold2.memlog.tsv` (2.5 MB, copied off tmpfs where it would have been lost).

### The failure

| field | value |
|---|---|
| goal | `oracle`, line 195: `print "oracle_gt" for g in cases do b23_case_score(g)` |
| `goal_has_value` | **True** |
| `goal_completed` | False |
| `goal_on_frontier` | True |
| `goal_unmet_deps` | **4** |
| `unresolved_cone_size` | 61 |
| `frontier_size` | 492 |
| the four unmet deps | `default.for_loop`, each `deps=2 pending=1 value=True persisted=False` |

**Four loop nodes that already have a value are still pending on one
dependency, and nothing is producing it.** The goal above them has a value too
and cannot complete. This is the §11 failure family — a `default.for_loop` that
holds a value and still waits — and clause **(T)**, not (P), is what caught it.
The (P) flood of §14 was noise from first to last; (T) fired exactly once, at
the right moment, and named the condition precisely.

### The finding that matters most

**This was a COLD store.** §1 and §3 record the failure as *warm-store only* —
0/11 at 8 threads on a warm store, while every cold configuration passed 16/16.
That framing is now wrong, and the reason is worth stating: a cold store stops
being cold. By hour seven this run had written 2.7 M rows and was reloading from
them, so it exercised exactly the read path §3 blames. **The variable is not
"warm versus cold" but "has the store been read from yet"** — and a long enough
cold run always gets there. §6's probe should be re-aimed accordingly.

### The performance measurement, which is the most complete one yet

7:07:44 of wall clock, 102,664 samples, instrument overhead 0.95% of run CPU:

| | |
|---|---:|
| wall | 25,667.5 s |
| **mean CPU (`getrusage`, exact)** | **1876.9%** of 2400% |
| completions | 11,597,670 — **451.8/s** average |
| **CPU per completion** | **41.54 ms** |
| peak RSS | 29.14 GB |
| major faults | 1,468,949 |
| **recomputes** | **1,539,129 — 13.3% of all completions were redo** |
| `persist_shed` / `persist_skipped_dead` | 3,996,992 / 679,187 |
| `ops_fused` / `interiors_elided` | 4,578,275 / 4,332,964 |
| saturated ≥90% of ceiling | 61.8% of intervals; below half 23.1% |
| **event-loop occupancy** | **63.7% mean** (49% in the July measurement) |

Loop phases, by cost: `predispatch` 6,433 s / 4.72 M calls, `fin_complete`
4,694 s, `reclaim` 4,566 s, `tbl_submit` 4,038 s. The loop is no longer at 49%;
at 63.7% it is becoming a constraint again, and `predispatch` is the phase to
attack.

`runnable_vs_cpu` repeats the §8 shape exactly: with 0–4 threads runnable (4,184
intervals) the process averages 779%; with 29+ runnable it averages 2,232%.

**Caveats that must travel with these numbers:** the run resolved **2 of 7
goals**, and `altered_while_running` is true — four `store.cache_max_gb` changes
(300→800→1400→3000→1300). It is a valid seven-hour sample of this workload's
behaviour; it is **not** a clean sweep measurement, and §8's deliverable is
still owed.

### What to do with it

The four stalled `default.for_loop` nodes are the same shape as issue 73 (*the
store persists a for_loop node as a sequence of handles that have no specs, and
the refusal surfaces on a pool thread*). This run is the strongest evidence yet
for it — a seven-hour reproduction with the full diagnostic block — and the
cold-store observation above contradicts the "warm only" framing recorded there
and in §1. **That evidence should be added to issue 73 before it is lost.**

### 24a. Posted, and the one thing that blocks a fix

Evidence added to issue 73 as
[comment 5638934000](https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/73#issuecomment-5638934000):
the cold-store reproduction, the correction to the warm-only framing, and the
diagnostic blind spot — `find_stalled` computes "unmet" over `graph.deps` only,
while a loop's residual wait is an `await_one` on its **spliced sequence**, which
is not a graph edge. The four stalled loops therefore report "every dependency
done, pending=1", and the report names neither the sequence nor its state.

**No fix is proposed, deliberately.** The two candidate causes — *the spliced
sequence never completed* versus *it completed and the wakeup was lost* — need
opposite fixes, and this run cannot distinguish them. The comment names the
three fields that would (`engine._alias[nid]`, that sequence's
incomplete/value/pending/persisted state, and whether `nid` is in
`graph._dependents[seq_id]`). That is the next run's job, not a guess.

---

## 25. Why a restart never benefits from the store, and what would fix it

The user's reading is right: **the expensive thing being repeated is the loop
unfolding, and the cache cannot help with it.** Three separate reasons, in order
of how much they cost.

### 1. The expansion is never persisted — deliberately

`_EXPANDED_OPERATORS` (`engine/core.py:127`) stops the store from holding
`for_loop` / `map` / `filter` containers. That was the right call as a
workaround: those rows are issue 73's poisoned sequences-of-handles. But the
consequence is that **every run re-derives the entire unfolding from scratch.**

The static plan is **107,728 nodes and takes 4.5 s to build**. Run 2 registered
**11,598,162** nodes. The gap — two orders of magnitude — is runtime expansion,
and it is redone in full on every restart, warm store or not. A persisted value
lets the engine skip a *kernel*; nothing lets it skip *discovering that the
kernel exists*.

That is the "past frontier" worth storing. Not the frontier itself, which is
ephemeral and reconstructible, but **the expansion: which loop produced which
element ids**. It is small — ids and arcs, not payloads — and it is the only
part of a seven-hour run that is genuinely irreplaceable.

### 2. Only a fraction of values ever get a row

Run 2's totals: 11,597,670 completions, **1,948,114 payload rows — 17%**. The
rest were pressure-shed (3,996,992) or skipped as dead (679,187). Shedding is
driven by writer-queue depth (§19), so *which* 17% survives is decided by
arrival timing, not by worth.

### 3. What survives is the cheap half, by construction

GreedyDual-Size orders by `compute_ms / payload_bytes`, so in run 2 the tier
**evicted 2.42 TB of `vox1.dt`/`vox1.dt2`** — 9–10 MB each, ~310 ms each, the
expensive ones — and kept 46 GB of small masks and thresholds. On a restart, the
values that would have saved the most time are exactly the ones already gone.

### What run 4 measures

Restarted the same sweep **warm on run 3's 1.5 TB store** (`/tmp/o60warm4.sh`,
deploy at `798af6d`, `--control-eval` on). Planning pruned nothing yet — it still
built all 107,728 nodes in 4.5 s. The number to watch is how far it gets before
it has to compute anything new, and whether it stalls in the warm read path,
where issue 73 lives and where the new diagnostic can finally name the sequence.

---

## 26. The paired `persist.enabled` experiment — the disk tier costs more than it buys

Issue 72's protocol, run inside run 4 without restarting it: two windows of ~100 s
either side of one `Knob.set`, everything else identical.

| | **A: persist ON** (105 s) | **B: persist OFF** (102 s) | change |
|---|---:|---:|---|
| throughput | 358 node/s | **509 node/s** | **+42%** |
| CPU (`utime+stime` over the window) | 1275% | **1999%** | **+57%** |
| recomputes | 1.6 /s | **64 /s** | ×40 |
| RAM evictions | 23 /s | 61 /s | ×2.6 |
| `persist_shed` | 94 /s | **0** (no offers) | — |
| payload tier growth | **8.3 GB/min** | **~0** (+11.8 kB total) | stopped |

**Turning the disk tier off made the run 42% faster and finally used the
machine.** CPU went from 1275% to 1999% of 2400%: with the writers gone, the
cores that were waiting on the write path are computing. The price is exactly
what the knob's documentation predicts — 64 recomputes/s instead of 1.6, because
a trimmed value now has nothing to come back from — and on this workload **that
price is smaller than the write path it replaces.**

It also stopped the disk dead: 8.3 GB/min to nothing, with the volume at 94%.

### The caveat that must travel with this

**One hundred seconds is a short window, and the recompute rate is the term that
grows.** Every minute with writes off adds values that have no disk copy, so the
64/s is a floor, not a steady state. What this measures is the *marginal* cost of
the write path at this point in this run; it is not a claim that a whole sweep is
faster without a store. The honest version of the experiment flips the knob back
after another few minutes and looks for hysteresis — if throughput does not
return to 358 when persistence is restored, the comparison was contaminated by
where the run happened to be.

What it does settle, and what issue 72 asked: **the disk tier is not free, and on
a sparse-cache sweep of this shape it is currently a net loss in the phase
measured.** 1275% versus 1999% of CPU is not a subtle effect.

### 26a. Run 4 continues with persistence OFF, by decision

The hysteresis control of §26 was **not** run: the user chose to let run 4
continue to completion with `persist.enabled = false`. So from 2026-09-11 23:16
onward this run writes no new values, and every trimmed value is a recompute.

**Anyone reading `o60_warm4.report.json` must know this.** The report is stamped
`altered_while_running`, but the stamp does not say that persistence was off for
the majority of the run, which changes the meaning of every throughput and
recompute number in it. The run is warm on run 3's 1.6 TB store, so values
written before the flip are still served; only values produced after it are
lost when trimmed.

The number to watch is `recomputes` per second. §26 measured 64/s right after the
flip and said that was a floor, not a steady state. If it stays near 64/s to the
end, the disk tier really is a net loss on this workload. If it climbs steeply,
the +42% throughput was a transient and the tier was paying for itself after all.
That is the question this run now answers.

---

## 27. §26's open question, answered: the +42% was transient and the tier was paying for itself

Run 4 at **10:23:37**, persistence off for about eleven hours. Measured over 92 s:

| | just after the flip (§26) | now |
|---|---:|---:|
| throughput | **509 node/s** | **19.4 node/s** |
| recomputes | 64 /s | **275 /s** |
| RAM evictions | 61 /s | 273 /s |
| CPU | 1999% | 1101% |
| redo per unit of progress | 0.13 | **14.2** |

Cumulative, and this is the line that settles it: **7,459,689 recomputes against
7,315,031 completions. The run has now redone more work than it has ever
finished.**

§26's caveat was the correct one. "64 recomputes/s is a floor, not a steady
state" — it rose 4.3× and throughput fell **26×**. The disk tier was not a net
loss; it was paying for itself, and the 42% gain measured over 100 s was the
engine spending a reserve of still-resident values that had disk copies. Once
those were gone, every trim became a recompute and the run entered §12's
storeless pathology by a second route.

**Three conclusions, all now measured rather than argued:**

1. **A 100-second window cannot evaluate a caching decision.** The honest
   experiment needs the hysteresis control, and this run is the reason to insist
   on it: the short-window answer was not merely imprecise, it was *inverted*.
2. Issue 72's question — "what does the disk tier actually buy?" — has an answer
   for this workload: at steady state it is the difference between 19 node/s and
   something above 350. The paired experiment must be reported with **both**
   windows or it misleads.
3. This is the third distinct route to the same failure: `--no-cache` (§12), the
   tier at its cap (§15), and now persistence switched off mid-run. In every one
   the mechanism is identical — **a trimmed value with no durable copy is a
   recompute** — and the engine has no policy that notices it is in that state.

### State

2 of 7 goals, 7,340,402 nodes registered, 19.4 node/s, CPU 1101%, RSS 24.5 GB,
store frozen at 1,613.9 GB, disk 94% (212 GB free), machine otherwise idle.
**At this rate the run does not finish.** The one action that could recover it is
`persist.enabled = true`: new values get rows again and trims become reloads.
The cost is resuming ~8.3 GB/min of tier growth into 212 GB of free space, which
buys ~25 minutes before the disk ceiling binds — and when it does, eviction of
dead rows has freed 300 GB in 34 s before (§23.2), so the ceiling is survivable.

---

## 28. The DAG's shape, read live — and a co-tenant eating half the machine

### 28.0 First, a contaminated measurement and a correction

`persist.enabled` was set back to **true** at 10:23. Throughput did not recover
(18.7 then 16.7 node/s). **That reading is void**: at almost the same moment
another of the user's processes — the anymatix ComfyUI server, PID 1384883,
`/home/vincenzo/anymatix/venv/bin/python main.py --listen 127.0.0.1 --port=10101`
— grew to **30.8 GB RSS and 76% CPU**. `MemAvailable` fell to 10 GB of 61, and
the governor did exactly what it should: budget 20.5 GB → **7.3 GB**, ceiling
17.6 GB, pressure 0.907. The re-enable experiment must be repeated on a quiet
machine before anything is concluded from it.

Note this is *not* the cause of the eleven-hour decline: that was measured at
09:54 and this process started at about 10:25. Two different things.

### 28.1 The shape, from `Runtime.eval` on the live engine

| | |
|---|---:|
| interned node specs (`table.nodes`) | **10,793,339** |
| completed | 7,337,144 |
| frontier (`graph.incomplete`) | 20,980 |
| **values with unrun consumers (`graph.consumers`)** | **47,141** |
| **of those, resident in RAM** | **4,808** |
| **of those, absent** | **42,333 (90%)** |
| resident values total | 5,045 |
| live value bytes | 4.0 GB |
| governor budget | 7.3 GB |
| open spliced loops (`engine._alias`) | **1,608** |

**The engine has promised to keep 47,141 values and can hold about 5,000.**
Nine of every ten values it is still required to produce again are not in memory.
That is not a cache being ineffective; it is a cache being asked for ten times
what it was given.

### 28.2 The order of computation is the cause, and here is the number

Distribution of unrun-consumer counts over those 47,141 pinned values:

| unrun consumers | values |
|---:|---:|
| **1** | **41,349 (84%)** |
| 2 | 4,195 |
| 3 | 1,403 |
| 4+ | ~200 |

**Eighty-four per cent of the retained working set is a value waiting for exactly
one consumer that the scheduler has not run yet.** Produce it, run its single
consumer, and it dies immediately — the memory would never be needed. Instead the
value waits, and §23.4 says how long: dead distance transforms lived **one to
three hours** between creation and last use.

`engine._alias` says the same thing structurally: **1,608 loops are open
simultaneously**. On a sixty-case sweep that is the whole experiment unrolled at
once rather than case after case.

By operator, the pinned set is dominated by `vox1./` (26,085 of 47,141),
`default.index` (3,159), `vox1.not` (2,772), `vox1.mask` (1,761) — while the
*bytes* on disk are dominated by `vox1.dt2` (9.56 MB each) and `vox1.dt`
(9.12 MB each).

### 28.3 And the plan itself is eating the memory the cache needs

10,793,339 interned specs at the ~1.5–1.9 kB per node measured in §18 is
**16–20 GB of plan**, against a process RSS of 18.0 GB and a *value* budget of
7.3 GB. The engine is spending the larger part of its memory remembering the
**shape** of the computation and the smaller part on the data.

### The answer to "how can this thrash with 64 GB?"

Three compounding reasons, all measured:

1. **It does not have 64 GB.** A co-tenant process holds 30.8 GB; available
   memory is 10 GB.
2. **Of what is left, most goes to the plan**, not to values: ~10.8 M interned
   specs.
3. **The traversal demands ten times what remains** — 47,141 values pinned by
   unrun consumers, 84% of them waiting on a single consumer that has not been
   scheduled.

Fixing (1) is a phone call. Fixing (2) is §18's packed node record. **Fixing (3)
is the one that matters, and it is the user's hypothesis: run each value's
consumer before opening new work.** A depth-first, case-at-a-time traversal would
hold hundreds of values where this one holds tens of thousands, and the entire
caching question would become uninteresting.

---

## 29. Can the queue be ordered so this cannot happen? Not the queue — the admission gate

**The queue's order is already right.** `ready.push` heaps
`(-priority, -next(self._seq), nid)`: highest priority first, and within a
priority band the **most recently pushed runs first**. That is LIFO, which is
depth-first. Re-ordering it buys nothing.

**Ordering is the wrong lever, because a value is pinned when a consumer is
REGISTERED, not when it runs.** `graph.register` increments `consumers[dep]` for
every dependency at registration time, and the value must then be retained until
that consumer executes. So the working set is set by *how much has been
registered*, not by the order in which it is executed. 47,141 values are pinned
because 47,141 consumers have been registered and not yet run.

### Where the breadth actually comes from

`engine/admission.py` gates in this order:

1. `job.in_flight >= self._live_window()` — **per loop**, `loop_window` bodies.
2. `self._blocked()` — the process's RSS ceiling.
3. `accounted >= self.hard_live_bytes` — the hard byte ceiling.
4. demand — admit while `qsize < workers`.

Two gaps, and together they are this failure:

- **The window is per loop, not global.** Nothing bounds how many loops are open
  at once. Measured: `len(engine._alias) = 1,608` open loops on a sixty-case
  sweep.
- **Every global gate measures RESIDENT bytes, never COMMITTED ones.** Under
  thrash, resident is *low* — 4.0 GB against a 7.3 GB budget — precisely because
  the engine is evicting everything it cannot hold. So the gate reads "plenty of
  room" and admits more work, which registers more consumers, which pins more
  values, which forces more eviction. **The thrash makes the gate see free
  memory.** The module's own docstring states the premise it rests on:
  "admission can't reclaim memory already committed to open work in the first
  place".
- And the demand rule compounds it: when workers are blocked waiting on values
  being recomputed, the ready queue looks *starved*, so admission opens more
  loops. Exactly backwards.

### The automatic fix, and it is one comparison

Gate admission on the **committed** working set instead of the resident one. The
engine already maintains the number: `len(graph.consumers)` — values it has
promised to keep — and their sizes are obtainable the same way
`accounted_bytes` is. The rule to add in `_can_admit`:

> Do not admit new work while the values already pinned by unrun consumers
> exceed what the value budget can hold, however short the ready queue is and
> however low resident bytes have fallen.

At the moment of measurement that reads 47,141 pinned against a budget that fits
about 5,000, so admission would have stopped long before, and the sweep would
have proceeded case after case at a fraction of the memory.

A cruder backstop worth having as well: **a global cap on simultaneously open
loops** (`len(engine._alias)`), which bounds 1,608 directly and is trivially
correct even if the byte accounting is wrong.

**No existing switch does this.** `loop_window` is configuration, has no CLI flag
and is not a live knob; the only live knobs are `engine.loop_delay_ns`,
`governor.rss_share`, `persist.enabled`, `persist.min_compute_ms` and
`store.cache_max_gb`. It is a code change, and a small one.

---

## 30. Why the 42,333 were evicted — and a correction: the reclaim is NOT cost-blind

### Correction first

§12 and §17 of this document say the governor's RAM trim "asks how many bytes it
can free, never what rebuilding will cost". **That is wrong, and reading
`core.py:_reclaim_memory` settles it.** Reclaim runs in passes, in this order:

| pass | what it takes | what it costs |
|---|---|---|
| **0** | *ownerless* values — consumer count 0, garbage | nothing: no write, no future read |
| **1** | spill queue: values whose write has landed and that are `persisted` | a **reload**, not a recompute |
| **2** | everything else: evict what is durable, start a write for what is not, and drop only what is cheaper than `sacrifice_ms` | a recompute, and only for the cheapest |

`sacrifice_ms = max(persist_min_ms, governor.sacrifice_ms)` and it **rises with
RSS pressure**, for a reason the code states: near the ceiling the alternative to
sacrificing a 200 ms recompute is being OOM-killed and losing every undurable
byte at once — 14.8 GB, measured. That is a considered policy, not a dumb one.

The genuinely cost-blind decision is elsewhere and §19 has it right: the
persister's `_shed` asks only "can this be rebuilt?", never "at what price".

### So why were 42,333 pinned values not in memory?

Because the choice was already lost upstream. **47,141 values were pinned by
unrun consumers against a value budget of 7.3 GB that holds about 5,000.** Once
admission has committed to that (§29), every eviction policy is only choosing
*which promise to break*. Reclaim broke them in the right order — garbage, then
durable, then cheapest-undurable — and still had to break 42,333 of them.

**The cost of breaking a promise depends entirely on whether the value has a disk
row**, and that is where this run is poor:

- a **durable** evicted value costs a reload from the 1.6 TB store;
- an **undurable** one costs a full recompute.

Recomputes are running at ~270/s, so most of these were undurable. Two reasons:
pressure shedding (§19) never wrote them, and — the larger cause here —
**`persist.enabled` was false for eleven hours by request (§26a)**, so every
value produced in that window has no row at all and can only be recomputed.

### The one-line answer

The scheduler is not choosing badly. **It is choosing among options that were
all made bad earlier**: admission promised to keep ten times what fits (§29), and
most of what it promised has no disk copy to come back from. Fix admission and
the eviction policy stops mattering — which is exactly the shape of every other
finding in this document.

---

## 31. The change that would dramatically improve this, and why depth-first is right but misplaced

### The one-sentence bug

**Every memory-based control in the engine reads what it is currently HOLDING,
never what it has PROMISED to hold — and eviction makes those two diverge exactly
when the difference matters.**

Under thrash `accounted_bytes` is *low* (4.0 GB against a 7.3 GB budget) precisely
because the engine is evicting everything it cannot keep. Every controller reads
that number and concludes there is room.

### Where it bites, in three places

**1. The adaptive per-loop window is fooled by the thrash it should prevent.**
`admission._live_window()` halves the window while `accounted > soft_live_bytes`
and *recovers a slot* when `accounted * 2 < soft_live_bytes`. Its docstring is
exactly right about the intent — "concurrency settles where the working set
actually fits, with no knob to set" — but the signal is resident bytes. Evicting
values lowers the signal, so the window grows back and opens more bodies. The
controller is closing the loop on the wrong variable.

**2. There is no global bound on open loops.** `admission.start()` does
`self._jobs[nid] = job` with no cap on `len(self._jobs)`. The window is per
loop; nothing limits how many loops. Measured: **1,608 open at once**.

**3. `_can_admit`'s gates** (`_blocked()`, `hard_live_bytes`) read resident bytes
too — §29.

### Why "explore depth first" is right, and where to apply it

The ready queue is **already** depth-first: `(-priority, -seq, nid)` is LIFO
within a priority band. Reordering it changes nothing, because **a value is
pinned when a consumer is REGISTERED, not when it runs**. Depth-first has to be
applied to *what gets opened*, not to *what runs next*.

### The changes, in order of leverage per line

**(a) Cap simultaneously open loops.** In `admission.start`, refuse a new job
while `len(self._jobs) >= cap`, re-using the wedge escape that already exists so
a true deadlock can still break out. This bounds the breadth directly.

*Extrapolation, not a measurement:* 47,141 pinned values across 1,608 open loops
is ~29 pinned values per open loop. A cap of 16 would put the pinned set near
**470 values** — trivially resident in any budget, with the working set fitting
in RAM and the disk tier becoming a convenience rather than a lifeline.

**(b) Feed the controllers the committed number, not the resident one.** Maintain
a `pinned` counter next to `accounted_bytes`: `graph.consumers` already tracks
the promises, so the count is free (`len(graph.consumers)`) and the bytes are one
add/subtract in the same place the refcount moves. Then `_live_window` and
`_can_admit` compare against `max(resident, pinned)`.

(b) is the principled fix and (a) is the blunt one. **(a) is worth doing first**
because it is a handful of lines, cannot be fooled by any accounting error, and
would have prevented every memory finding in this document.

### What I would want to see before believing it

A run with (a) at cap 16, measuring: `len(engine._alias)`, `len(graph.consumers)`,
`resident_values`, `recomputes/s` and node/s. The prediction is pinned values in
the hundreds, recomputes near zero, and throughput limited by kernels rather than
by memory. If pinned stays in the tens of thousands with 16 loops open, the
per-loop subtree is the problem rather than the loop count, and (b) is required.

---

## 32. THE SWEEP FINISHED — 7 of 7 goals, and the first complete measurement

Run 4 completed at **12 h 15 m 37 s**. `complete: true`, `error: null`,
`goals_resolved: 7 / 7`. This is the deliverable §8 has been owed since the
beginning: a whole sixty-case double sweep carried by one plain
`voxlogica run`, no sharding, no supervisor, no restart.

### The science

| | n | mean | min | max |
|---|---:|---:|---:|---:|
| `oracle_gt` (the parameter-sweep oracle) | 60 | **0.8991** | 0.6747 | 0.9749 |
| `nn_dice` (nnU-Net, same cases) | 60 | **0.9293** | 0.7938 | 0.9815 |

The oracle sits at 0.8991 against nnU-Net's 0.9293 on these sixty cases — in line
with the 0.9014-over-369 figure this project treats as the reference.

### The engineering, and it is expensive

| | run 2 (incomplete, 2/7, 7:07) | **run 4 (complete, 7/7, 12:15)** |
|---|---:|---:|
| wall | 25,667 s | **44,137 s** |
| mean CPU | 1876.9% | **1345.2%** of 2400% |
| completions | 11,597,670 | 7,634,831 |
| completions/s | 451.8 | **173.0** |
| **CPU per completion** | 41.54 ms | **77.77 ms** |
| **recomputes** | 1,539,129 (13% of completions) | **9,160,188 — 120% of completions** |
| peak RSS | 29.1 GB | 31.2 GB |
| cache hits / bytes read | — | 277,908 / **1.46 TB** |
| `evicted_live` | 0 | 0 |

**It redid more work than it did.** 9.16 M recomputes against 7.63 M completions,
and the energy cost per unit of work was **1.9× run 2's**.

**Caveats that must travel with these numbers:** five live knob changes
(`store.cache_max_gb` 300→800→1400→3000→1300, then `persist.enabled`
false→true), `altered_while_running: true`, and **persistence was off for about
eleven hours of the twelve**. These are a valid record of a completed sweep; they
are not a clean benchmark, and §8's deliverable is only half discharged — the
run completed, the measurement is contaminated.

### What it settles about concurrency and cache

The run is the argument for §31, not against it. Opening 1,608 loops at once
maximised **neither**:

- **Concurrency was not maximised: 1345% of a 2400% ceiling** — a little over half
  the machine. Concurrency is bounded by the 32 worker threads, and feeding them
  needs roughly 32 ready nodes; `ready` measured 30–36 all run. **1,608 open
  loops fed exactly the same 32 workers that 16 would have.** The surplus breadth
  bought no parallelism at all.
- **Cache exploitation was not maximised: 120% redo.** The working set — 47,141
  values pinned by unrun consumers — was ten times what memory could hold, so the
  cache could not hit.

Breadth is not concurrency. Capping open loops costs nothing in parallelism and
returns the working set to something that fits, which is the only way the cache
can be exploited at all. **The two goals do not compete here; they have the same
answer.**

---

## 33. The fix, implemented and tested — `02a672e`

### What it is

**One number the engine never had: `NodeTable.unkept`** — how many values a
registered consumer still needs and that are not resident. It is raised by
eviction (the only way a promise can break), cleared by providing the value again
or by the last consumer going away, and **its defining property is that evicting
more cannot lower it.**

That property is the whole point. `accounted_bytes` says what the table is
*holding*; under thrash it FALLS, because the engine is discarding values it
still owes. Every controller that reads it therefore sees room exactly when the
engine is failing hardest. `unkept` moves the other way.

**The controller then reads it.** `LoopAdmission._live_window` already halved the
window under pressure and recovered a slot when memory was comfortable — the
right mechanism with the wrong input. It now halves while anything is owed, and
recovers only when nothing is.

Three files, small diffs: `node_table.py` (the counter and its two update
sites), `graph.py` (hands the table `consumers.get` as the probe; clears the
debt on last release), `admission.py` (the window reads it).

### Tested, and the tests fail on the parent

Four tests in `tests/unit/test_memory_backpressure.py`:

| test | what it pins |
|---|---|
| `test_unkept_counts_a_value_evicted_while_a_consumer_still_waits` | eviction with a waiter raises the debt; providing the value clears it |
| `test_unkept_ignores_a_value_nobody_is_waiting_for` | dropping garbage is not a broken promise |
| `test_unkept_cannot_be_lowered_by_evicting_more` | **the discriminating case**: resident bytes fall while the debt rises |
| `test_the_admission_window_does_not_recover_while_a_promise_is_broken` | the regression itself: comfortable bytes + starving queue no longer grow the window while anything is owed |

- **On the parent `798af6d`: 4 failed.**
- **On the fix: full suite 1,275 passed, 3 skipped, 1 failed.**

The one failure is `tests/e2e/nnunet_shapes/test_circle_segmentation_e2e.py`,
which needs `nnUNetv2_plan_and_preprocess` on PATH; it is **absent from a
non-login ssh shell and the test fails identically on the parent.** Pre-existing
and environmental.

### What this does NOT do, stated plainly

**It does not cap the number of open loops, and that is still the dominant
term.** `_window_now` is one value on the admission object, shared by every job,
so with 1,608 loops open even a window of 1 leaves 1,608 bodies in flight. This
change stops the window *growing* while the engine is failing — it prevents the
runaway, it does not collapse one already in progress.

**And it is not yet validated on a real run.** Everything above is unit-level.
The falsifiable prediction for the next sweep, against run 4's completed numbers:

| | run 4 (measured) | prediction with the fix |
|---|---:|---:|
| recomputes / completions | **9,160,188 / 7,634,831 = 120%** | well under 100%, and falling with it |
| CPU per completion | 77.77 ms | toward run 2's 41.54 ms |
| mean CPU | 1345% of 2400% | higher, because workers stop redoing work |
| `unkept` at steady state | (would have been ~42,000) | near zero, by construction |

If `unkept` stays high on the next run, the window is not the binding term and
the global loop cap of §31(a) is required. That is the experiment, and it is one
sweep.

---

## 34. Run 5, first reading at 13:53 — the prediction holds so far

Cold store, `02a672e` + `f194836`, 32 threads, no artificial cache cap.
Measured over 120 s after the warm-up plateau:

| | run 2 (cold, mean over 7 h) | run 4 (completed, mean over 12 h) | **run 5 at 13:53** |
|---|---:|---:|---:|
| throughput | 451.8 node/s | 173.0 node/s | **565 node/s** |
| CPU | 1876.9% | 1345.2% | **2207%** of 2400% |
| recomputes / completions | 13.3% | **120%** | **4.6%** |
| recomputes per second | — | 275 /s | **19.5 /s** |
| `unkept` | (did not exist) | (would have been ~42,000) | **0** |
| open loops | — | **1,608** | **243** |
| admission window | — | — | 32 (full — nothing owed, so nothing throttled) |

**Every term of §33's prediction is on the right side of the line**, and the CPU
is the number the user asked about: **2207%**, above the ~2050% P-core-equivalent
ceiling, which means the E-cores are contributing rather than waiting.

`unkept = 0` is the mechanism working exactly as designed: nothing that was
produced and is still needed has been thrown away, so the window stays at its
full 32 and concurrency is not throttled at all. Note `pinned` is **154,705** —
higher than run 4's 47,141 — and that is *not* a problem: a pinned value whose
kernel has not run yet was never a broken promise. The counter measures what was
produced and then discarded, which is the only kind that costs.

**This is 14 minutes of a run that took 12 hours to finish last time.** Run 2 also
held ~570 node/s for its first fifty minutes before its cache cap bit. The claim
that survives so far is only that the *start* is healthy; the question this run
exists to answer is whether `unkept` stays at zero once the working set grows.

---

## 35. "It did not reuse the cache" — true in effect, and not yet measurable

Run 7 is warm on run 5's store (862,144 ids in the persisted index, 1.37 TB of
payloads) and is nevertheless executing a kernel for almost every node it
completes: `kernels_executed` 493,660 against `completed` 495,906 at 19 minutes.

**The counter that would settle it does not exist.** Reuse happens at
registration — `_available(nid)` prunes a node whose value is on disk, so it is
never scheduled and never appears in any completion counter. A run that reused
everything and a run that reused nothing differ only in how *few* nodes the
first one registers, and nothing records that. I attempted two live measurements
and both were void: `completed ∩ persisted_ids` counts this run's own writes,
because the index grows as the run persists, and a full set intersection against
`table.nodes` is a ten-million-element construction on the event loop, which I
should not have run and will not run again.

**The two structural reasons already measured stand** (§25):

1. **The expansion is never persisted** (`_EXPANDED_OPERATORS`), so the unfolding
   from 107,728 static nodes to millions is redone in full every time, warm or
   cold. A stored value lets the engine skip a *kernel*; nothing lets it skip
   *discovering that the kernel exists*.
2. **Only a fraction of a run's values ever get a row.** Run 5 reached 2.64 M
   nodes and left 856 K ids in the store — about 32%, the rest dropped by
   pressure shedding, which chooses by writer-queue timing rather than by worth.
   Two thirds of a warm run's work therefore has nothing to come back to.

**The missing instrument, and it is one counter:** increment on every node
`_schedule_subgraph` prunes because `_available` said the store has it. Then
"how much did the warm store buy" is a number rather than an argument, for this
run and for every future one. Until it exists, any claim about reuse here —
including mine — is inference.

---

## 36. Run 7 hung — a hard deadlock, and it is NOT memory

Run 7 (warm, `unkept` back-pressure, grant gate off) ran 5 h 52 m and then stopped
completing anything. The 3,600-second watchdog fired at 6:52:09:

```
RuntimeError: engine stalled: no node completed for 3600s
(9021291 done, in_flight=0, ready=0, jobs=10, outstanding=10, parked=0,
 units={'pushed': 6113567, 'parked': 64, 'popped': 6113631,
        'jobs_started': 2055, 'jobs_ended': 2045, 'unparked': 64})
```

Details id **VLX-D4E84F14**. Report: 24,730 s wall, **1517.8% mean CPU**,
2 of 7 goals, 9,022,374 nodes, peak RSS 24.2 GB. `nn_dice` printed, so that
goal's output exists.

### The stuck frontier

```
[stuck] qsize=0 outstanding=10 completed=9021291 stuck=1083 alias=1888 jobs=10
  b95794ff op=default.sequence pending=1 alias=False unmet=['784b0770']
  ca4b6c95 op=vox1.not        pending=1 alias=False unmet=['53792292']
  1c873155 op=default.argmax  pending=1 alias=False unmet=['bca35ded']
  ...
```

**1,083 nodes, each waiting on exactly one dependency that nothing is
producing.** Ten of 2,055 expansion jobs started and never ended. Nothing in
flight, nothing ready, nothing parked.

### It is not memory, and the memory log says so plainly

The last rows before the watchdog:

| | |
|---|---:|
| live values | **256.4 MB** |
| accounted | **1,882.7 MB** |
| governor budget | **11,244.6 MB** |
| `gov_pressure` | **0.667** |
| `gov_gap` | **16,971 MB** |
| in flight / ready | 0 / 0 |

Two gigabytes held against an eleven-gigabyte budget with seventeen gigabytes of
headroom. **Admission was not blocked by memory**, the governor was not
trimming, and the earlier hypothesis — that a co-tenant process squeezed the box
and the one-shot wedge escape was spent — is refuted by these numbers. The
engine simply had nothing it was willing to run.

### What I cannot exclude, and the test that settles it

`unkept` back-pressure pins `_live_window()` at **1** for as long as any debt
stands, and run 7's debt stood at 305 for hours. The window bottoming at 1 is
by design ("a loop must always be able to make progress"), and with ten jobs
open that still permits ten bodies in flight — yet `in_flight` was 0, which says
the jobs were not being refused by the window but were not offering anything.
That points at the expansion job's own wait rather than at the admission gate.

**But I introduced the condition that holds the window at 1 indefinitely, and I
will not claim it is innocent without evidence.** The discriminating run is the
same warm store with the `unkept` term removed from `_live_window` — if it hangs
the same way, the deadlock predates today; if it does not, I caused it.

The cheaper and better first step is a diagnostic, in the same spirit as
§24a: when the watchdog fires, dump for each open job its `cursor`,
`expansion.total`, `len(staged)`, `in_flight`, and whether its `wake` event is
set. That distinguishes "the job has bodies and admission refuses them" from
"the job has no bodies and is waiting for a wake that will never come", and
those need opposite fixes. Neither is guessable from what run 7 recorded.

---

## 37. The resume ladder, honestly: the memo works, the resume is 30% not 10x

Built and verified this session: the dev stop guard, `output_kind` (without which
no stored spec could be verified), the expansion memo with its spec closure, and
the verified read path. Suite 1,299 passed throughout.

### What the ladder measured

| | |
|---|---:|
| rung 1, cold, reaching 100,000 completions | **5:22** at 519-560 node/s |
| the resume, reaching 100,000 completions | **≈3:45** at 554-562 node/s |
| values served from the store in that time | **2,409** of 70,193 completions |
| stored results after rung 1 | 29,325 of 100,002 completions (29%) |
| memo hits on the resume | 6 |

**So a resume is about 30% faster, not a restart from the frontier.** The
mechanism is sound -- memos hit, closures verify, values are served -- and the
effect is small.

### Why, and it is not the memo

Only **2,409 of 70,193** completions came from disk. The stored values are
largely not the ones a resumed walk asks for. Three filters were each suspected
and each ruled out by measurement:

1. **pressure shedding** (`--sparse-cache`): removed. Coverage 32% -> 29%, and
   throughput went UP (560 node/s, the fastest of any rung). Not the filter.
2. **`persist_min_compute_ms`**: set to 0, so nothing is refused for being
   cheap. Coverage unchanged at 29%. Not the filter.
3. What remains is that most completions have **no separate value to store**:
   constants and closures are never persisted ("roughly half of all nodes" in
   loop-heavy plans) and fused cone interiors complete inside their cone (4.3 M
   elided of 11.6 M on an earlier run). If so, 29% is the shape of the work
   rather than a leak -- and the resume's problem is not how MUCH is stored but
   WHICH.

### What that leaves

This is §7's conclusion arriving from the other direction: **the store holds a
sample of the DAG, not a cut.** A resume prunes only where the walk meets a
stored value, and a sample has holes everywhere, so the walk descends past them
and recomputes the interior. The fix named there -- persist loop-body roots and
goal inputs as *critical*, never sheddable, never evictable -- is the one thing
in this design that has not been built, and it is the one that decides whether a
resume prunes sixty subtrees at their roots or picks its way through a sieve.

**Not attempted here**, and deliberately: this session has four refuted fixes
behind it, and the cut policy deserves the measurement first -- which node ids a
resumed walk actually requests, and how many of them are stored. That query is
one pass over a run's registration order against the store, and it turns "which"
from an argument into a number.

### 37a. The ladder's three rungs, measured — and the disk wall

| rung | C | mean node/s | CPU | ms CPU/completion | recomputes |
|---|---:|---:|---:|---:|---:|
| 1 | 100 k | 330.1 | 1849% | 56.03 | 1,853 |
| 2 | 500 k | **476.3** | 2047% | 42.97 | 19,432 |
| 3 | 1 M | **472.8** | 2067% | 43.72 | 26,189 |

All three stopped exactly on the guard, none failed. **Throughput does not
degrade across rungs** — 476 → 473 with CPU rising and energy per completion
flat at 43.7 ms. Rung 1's 330 is its cold start averaged over a short run.

**Correction worth keeping.** Earlier entries quote 515–562 node/s "sustained";
those are the progress bar's INSTANTANEOUS rate, which peaks and sags. The
authoritative mean from `getrusage` is **~475 node/s**, below the 500 target.
The bar is not the instrument and should not have been quoted as one.

**The ladder stops here for a physical reason.** One million completions wrote
**752 GB** of payloads with 500 GB free. Rung 4 (5 M) needs about 3.7 TB. With
shedding off and `persist_min_compute_ms` at 0 — the settings that make a resume
possible at all — the disk is exhausted before the next rung.

So the fork is now forced rather than chosen:

- **shed again** → the store keeps ~32% by queue timing, fits on disk, and the
  resume stays 30% rather than a restart;
- **the cut (§7)** → keep loop-body roots and goal inputs as *critical*, never
  shed, never evicted, and let the interior go. Far less data, and the part a
  resume actually prunes at.

The second is the design this document has argued for twice from different
directions, and it is still unbuilt. It is also the only one of the two that
fits in 500 GB.

### 37b. Cut-preserving eviction: correct, free, and not the reuse fix

Piece (1) of the three: a value with unrun consumers is written before it is
dropped **when its own inputs are not stored** — so the boundary never develops
a hole. Narrowed to that case because the first version spilled every frontier
value and was caught by a test backed by a measurement: *a pressure spill wrote
300 GB in forty minutes*, and the disk tier is a pure optimisation.

| | baseline | with (1) |
|---|---:|---:|
| frontier values written at the stop | 696 | 794 |
| resume reuse (`pruned / registered`) | 38.6% | **38.1%** |
| throughput | ~506 node/s | 517 node/s |

**No effect on reuse.** Kept anyway: it is a correctness invariant (the stored
set stays a cut), it costs no measurable throughput, and it removes a way for a
checkpoint to be silently incomplete. But it is not what is holding reuse at
38%, and the 98 extra values it saved say why — the frontier that matters is
much larger than the one still resident at any moment.

That points at (2): the checkpoint takes whatever survived rather than
*choosing* a cut. 794 values is not a chosen cut, it is a census of survivors.

### 37c. All three pieces, measured — and the ceiling is fusion

**What `pruned/registered` means.** The two counters are *disjoint*:
`_schedule_subgraph` increments `_pruned_available` and `continue`s BEFORE
`graph.register`, so a node is either served by the store and skipped, or
registered and scheduled — never both. So 38.6% is *pruned against scheduled*,
not a share of a whole: as a fraction of every node the walk reached it is
38.6/138.6 = **27.8%**. Neither number is "how much of the store got reused" —
the store's size is in no denominator here. And both UNDERSTATE the work
avoided, because a pruned node ends the walk: it stands for an entire subtree
that was never visited and therefore never counted.

| | reuse (`pruned/registered`) |
|---|---:|
| frontier checkpoint alone (baseline) | **38.6%** |
| + cut-preserving eviction | 38.1% |
| + instrumentation, frontier 1,908 values | 37.5% |

**(1) cut-preserving eviction — refuted as a reuse fix, kept as a free
invariant.** `evicted_early` is **0** at C=100k: nothing is ever dropped at this
scale, so the rule is never consulted (`cut_would_break` 0, `cut_unknown` 0) and
there was never a hole for it to prevent. It costs no measurable throughput and
it removes a way for a checkpoint to be silently incomplete, so it stays — but
it is not what holds reuse at 38%.

**(2) selected checkpoints — inapplicable for the same reason.** With no
eviction, the checkpoint already writes the *entire* resident frontier; there
are no survivors to choose between. "Choose the cut rather than take survivors"
presumes losses that do not occur here.

**(3) demand-driven evaluation — its stated mechanism is already implemented.**
`_schedule_subgraph` prunes at any available node and does **not** examine that
node's inputs: the `continue` skips extending the frontier with its deps. What
remains of (3) is avoiding the up-front registration of a goal's whole cone,
which is a startup and memory cost — the O(plan) expansion — and not a reuse
one.

### The ceiling, stated so it is not re-litigated

**Roughly 24,000 operations are fused per 100,000 completions, and a fused cone
interior produces no value of its own.** You cannot prune at a node that never
had a value. Every lever tried against the 38% ceiling — pressure shedding off,
`persist_min_compute_ms` at 0, a cut at loop-body roots, memoised expansions
with verified spec closures, cut-preserving eviction, a larger checkpoint — left
it within noise, because none of them touches a node that was never stored.

Moving past 38% means changing **what fusion materialises**, not the cache, the
cut, the checkpoint, or the traversal. The honest experiment is a controlled
comparison with `VOXLOGICA_FUSION=0`: it will cost throughput and should raise
reuse, and the trade between them is the real design question this work uncovered.

### 37d. Correction: fusion is NOT the ceiling — cones are already atomic

§37c named fusion as the thing holding resume reuse at 38%. Reading
`engine/fusion.py` back, that is wrong, and the correction matters because it
changes the decision: **fusion does not have to be given up.**

A cone already splits its members into `exits` and `interiors`
(`fusion.py:167-189`). An `interior` is a member with *no consumer outside the
cone* — by construction. Exits (goals, members with an outside dependent, and
any member carrying an extra `graph.consumers` hold the planning snapshot
cannot explain) take the normal `set_value` → persist path
(`core.py:2893`, `executor.py:153`); only interiors are elided
(`core.py:2868-2881`).

So a fused interior is a value **nothing outside the cone can ever ask for**.
A resume walk reaches a node only through a consumer; an interior has none
outside, so the walk can only arrive at it *after* descending through its own
exit — and if that exit is stored, the walk prunes there and never sees the
interior at all. Fusion therefore makes restart points **coarser** (mean cone
3.24 members, so ~1 anchor where there might have been ~3), not **absent**.
It cannot force a recompute of work that is already on disk.

This also retires the proposed `VOXLOGICA_FUSION=0` comparison as the next
experiment. It would trade throughput for a denser anchor set, but density is
not what is missing.

**What is actually left.** Resume reuse is a property of the STORE, not of the
traversal (`_schedule_subgraph` is already optimal: it stops at the first
available node and never looks at that node's inputs). Clean reuse needs one
invariant: *for every sweep element that finished, its body root is on disk.*
Three things can punch a hole in that, and they are not equally likely:

1. **persister shedding** — writes dropped when the writer queue is deep. This
   is pressure-dependent, so it bites hardest exactly on the long runs where
   resume matters most. Prime suspect.
2. **`persist_min_compute_ms = 1.0`** — a body root cheap enough to fall under
   the threshold is skipped. Cheap to recompute, but it still removes an
   anchor, and the subtree below it is not necessarily cheap.
3. **disk eviction** — measured `evicted_early = 0` at C=100k, so not binding
   at that scale. Kept on the list because it returns at 5M/10M.

And one cost that is NOT recompute but looks like "starting from scratch" to
anyone watching: on resume the engine must still rebuild the DAG down to the
frontier before it can prune. At 12M nodes that is minutes of walking with the
CPU busy and nothing being computed — indistinguishable, from the outside,
from a cold start.

**The measurement that decides it, not yet run.** On a resume, of the nodes
the engine *registers*, how many were `completed` in the previous run? Split
that count by the three causes above. That converts "is there a path" into
"which hole, and how big" — and each of the three has a different, known fix
(shed policy must exempt critical anchors; threshold must exempt body roots;
eviction already has the cut-preserving rule from §37b).

### 37e. The resumer cannot know, and that is why there is no resume message

*What "checkpoint" names here.* A row, not a copy of any value:

    checkpoint(goal_id PK, anchor_ids BLOB, n INTEGER, ts)

`goal_id` is the id of the node the query asks for (sha256 over its spec
closure, as every id is). `anchor_ids` is the serialised set of ids that were
in `graph.consumers` at stop time — completed, with at least one unrun
consumer — restricted to those whose value the final spill actually landed.
Written by `checkpoint_frontier()` after its spill loop; read once at startup,
before the first `_schedule_subgraph(goal)`.

Two consequences. **Reachability comes free from hash-consing**: identical
`goal_id` means an identical DAG, so every recorded anchor is reachable from
the goal by construction, with no validating walk. And **`_available` stops
being a per-node store probe**: the walk tests the in-memory anchor set first
and only touches the store on a hit, to load. Misses become free.

The row asserts *reachability*, never *availability* — an anchor's value may
have been evicted since. Availability stays a check at the anchor, so a stale
row degrades to exactly today's behaviour and never to incorrectness.

Two complaints with one root: (a) a resume looks identical to a cold start from
the outside, and (b) nothing tells the engine, up front, whether a usable
frontier is reachable at all.

**Today it is discovered, never known.** `_schedule_subgraph` walks down from
the goal and asks the store once per node (`_available`). Reachability is a
by-product of the walk: the engine learns an anchor exists at the moment it
arrives at it, and not one step sooner. So there is nothing to announce at
startup, because at startup the engine genuinely does not know.

`checkpoint_frontier()` (core.py:1776) does not close this. It spills the
*values* of `graph.consumers` and returns a count — it records **no id set and
no goal**. Nothing survives the process that a later run could read before
walking. The frontier is written; the *fact of* the frontier is not.

**Fix: make the checkpoint a first-class record keyed by the goal's hash.**
At stop: write `(goal_id → {anchor ids}, counts, run metadata)`. At start: one
lookup on the goal id.

The soundness argument is hash-consing, and it is exact. Node ids are Merkle
hashes over the spec closure, so *the same goal id means the same DAG*. An
anchor recorded under goal G is therefore reachable from G by construction —
no verification walk needed to establish reachability. An edited `.imgql`
hashes to a different goal id, finds no record, and honestly reports a cold
start. This is the correct behaviour, not a gap.

What it buys, in order of importance:

1. **A truthful startup message**, before a single node is scheduled:
   "resuming <goal>: checkpoint with N anchors" versus "no checkpoint for this
   goal — cold start". Currently impossible to emit at any price.
2. **The walk stops hitting sqlite.** Membership becomes an in-memory set test
   instead of one store probe per node — which is the direct attack on §37d's
   "minutes of busy CPU computing nothing", the thing that makes a resume
   *look* like a cold start even when it is reusing properly.
3. **A number to hold the store to.** "N anchors recorded, M found at resume"
   is the hole measurement §37d asks for, obtained for free rather than by
   instrumenting three subsystems.

The one caveat, stated so it is not discovered later: the record is a *claim
about reachability*, not about *availability*. A recorded anchor's value can
have been evicted since. So the walk still verifies laziliy at each anchor —
the record removes the search, not the check.

### 37f. Measured at last: the backward walk is NOT slow, and §37d was wrong

§37d asserted that a resume spends "minutes of walking with the CPU busy and
nothing being computed". That was an inference stated as a fact, and it is
**false**. The data to check it already existed — the ladder runs' own
`--measure-series` samples — and nobody had read it.

From `rung1.report.samples.tsv` (cold, stopped at C=100k) and
`rung2.report.samples.tsv` (resume off rung1's store, stopped at C=500k),
elapsed from each file's first sample:

| completions reached | rung1 (cold) | rung2 (resume) |
|---|---:|---:|
| first completion | — | **0.8 s** wall, 3.9 s cpu |
| 1,000 | (already past) | 26.3 s |
| 10,000 | 131.0 s | **47.9 s** |
| 50,000 | 208.8 s | **142.3 s** |
| 100,000 | 299.0 s | **244.3 s** |

Two things follow, and they point opposite ways to what this document has been
assuming since §37c.

**1. The walk costs under a second.** The resume completes its first node 0.8 s
in. There is no multi-minute traversal phase to optimise, and therefore no case
for caching the traversal — see below.

**2. The resume is FASTER than the cold run to every mark**, 2.7x at 10k. So it
is reusing, and the "it restarted from scratch" reading of these runs is not
what the instrument says. Whatever is disappointing about resume, it is not
that the walk redoes the work.

*Reading caveats, both of which make the resume look worse than it was, not
better.* rung1's first sample already shows `completed=1901`, so its clock
starts late and its cold times are UNDERSTATED; rung2's starts at
`completed=0`. And rung2 ran on to 500k, so only the marks at or below 100k are
a like-for-like comparison.

**Why the walk is cheap, structurally.** It stops at the first stored node and
never looks below it, so it only ever descends through work that has NOT been
done — which has to be registered anyway in order to be computed. Its cost is
proportional to *what is left*, not to the size of the DAG. Caching it would be
caching a function of the store's current state, which is both unsound to reuse
and, per the table above, worth nothing.

**What this does to §37e.** Point 2 of that section — "the walk stops hitting
sqlite", offered as the direct attack on startup cost — is now unsupported: it
is optimising something that takes 0.8 s. The other two reasons stand and are
the only ones left: a truthful startup message, which is impossible to emit
today at any price, and "recorded N, found M" as the hole measurement §37d
asks for. The checkpoint record is an *observability and honesty* change, not a
performance one, and it should be argued for on those terms or not at all.

### 37g. The walk, measured: 127–183k nodes/s, ~1% of wall — and the real defect

Two runs at C=100,000 on the new `_walk_*` counters (`/tmp/walk.sh`, sequential
on 24 cores): `walkcold` on a fresh store, `walkwarm` resuming off `ladder.db`.

| | walkcold | walkwarm (resume) |
|---|---:|---:|
| walk pops | 354,580 | 341,287 |
| walk wall time | 1.935 s | 2.692 s |
| **pops per second** | **183,245** | **126,778** |
| walk share of run wall | 0.62% | 1.08% |
| `pruned_available` | 0 | **77,805** |
| `registered_total` | 211,584 | 206,675 |
| completions / s | 344.5 | **405.8** |
| run wall to C=100k | 310.0 s | **249.5 s** |
| `ops_fused` | 44,206 | 29,273 |
| recomputes | 1,396 | 1,083 |

**The walk is not a cost.** Under three seconds out of four minutes, ~1% of
wall. The resume's walk is 31% slower *per pop* (126.8k vs 183.2k/s) and that
difference is the store probe — the exact cost §37e proposed to remove with an
in-memory anchor set. Removing it would save about 0.8 seconds. §37f already
withdrew the performance case for the checkpoint record; this closes it
numerically. The record is worth building for the startup message and the
"recorded N, found M" leak count, and for nothing else.

**Per-node memory: NOT measured, and the obvious arithmetic is wrong.**
`(rss - accounted - baseline) / registered` gives 49.6 KB/node cold and
47.3 KB/node warm — consistent, and meaningless as a structural figure. The
residual is dominated by value memory that `accounted_bytes` does not count
(pooled buffers, SimpleITK images), plus allocator fragmentation, torch and
thread stacks. It is an upper bound on index cost and nothing more. The direct
measurement — `sys.getsizeof` over `graph._dependents`, `graph.consumers`,
`table.nodes`, `_priority`, `incomplete`, `completed` — was attempted through
the control socket and lost the race with `DEV_STOP`; it needs a probe taken
while a run is mid-flight, and should be taken next time one is up.

**The resume is NOT computing from scratch.** 77,805 nodes were answered by the
store and never scheduled (0 on the cold run), the same C=100,000 took 249.5 s
instead of 310.0 s, and fused ops nearly halved — 29,273 against 44,206 —
because a large part of the fusable work was already on disk.

**But it reuses far less than the store can support, and there is now a named
mechanism.** Probed live at completed=100,244:

    memo_hits = 1        memo_misses = 1,989
    expanded_loops = 17  expanded_bodies = 925

The expansion memo — `put_expansion`/`get_expansion`, built in §37 precisely so
that a resume does not re-derive loop structure — **hits once in 1,990
attempts**. Every loop is being re-expanded from scratch. That matters far more
than any traversal question, because expansion is what *produces the node ids*:
a body root that is never named can never be looked up, never pruned, and never
reused, no matter what the store holds. `ladder.db` carries 500,000+
completions and this resume pruned 77,805 nodes.

Next: find why `get_expansion` misses. It is a lookup keyed by a loop node id,
so either the ids differ between runs (an expansion that is not a pure function
of the loop spec) or the memo rows were never written (the §37 write path, which
`_memoise_expansion` was rebuilt to guarantee). One SELECT against `ladder.db`
separates those two, and they have nothing in common as fixes.

### 37h. The resume test exists, it passes, and §37g's diagnosis was wrong

`tests/e2e/test_resume_starts_from_the_frontier.py`. The claim this branch has
argued from progress bars since §37c is now a test that can fail: *run half a
computation, stop, start it again, and the second run must do the OTHER half.*

**The program.** Ten independent `for` loops of 870 elements over 32x32
`blank` images, ~20,000 completions. Three properties are load-bearing and each
was measured into place, not guessed:

- *Ten loops, not one.* With a single `for`, a stop at half the work always
  lands mid-expansion, so no memo is ever written and there is nothing for the
  resume to hit. Measured on the first draft: `expanded_loops=0`, zero rows in
  `expansion`. The test was asserting on a case the program could not produce.
- *Distinct constants per loop*, or they hash-cons into one node and it is the
  single-loop program again.
- *870, calibrated.* 500 elements x 10 measured 11,553 completions — ~2.31 per
  element, not the ~4 the source suggests, because fusion elides cone interiors
  and those never complete. Scaling gives 870 → 20,063 measured.
- *Images, not scalars*, so a node is worth persisting; a scalar program would
  measure `persist_min_compute_ms`'s correct refusal to store values cheaper to
  recompute, and prove nothing about resume.

**Result — the resume is very nearly exact.**

| | completions |
|---|---:|
| reference run (whole program) | 20,063 |
| stopped run (guard at 10,000) | 11,963 |
| **left to do** | **8,137** |
| **the resume actually did** | **8,101** (99.6%) |

with `pruned_available = 3,996`. A resume that replanned from the goals would
have done ~20,000. **The frontier resume works.**

**And the memo is not the defect §37g named it.** A finished run writes its
memos and a rerun uses them — measured directly: 10 loops expanded, 10 rows in
`expansion`, and the rerun hits 10, misses 0, expands nothing and completes 20
of 20,063 nodes. The mechanism is sound.

What is true is narrower: **a memo is written when a loop's expansion COMPLETES**
(`_memoise_expansion` from `_on_spliced`), so a run stopped mid-expansion writes
none. On BraTS the loops are large enough that stopping at 100,000 completions
leaves every one of them partly expanded — and that, not a broken write path or
mismatched ids, is the whole of §37g's "1 hit against 1,989 misses".

The cost of that gap is a DAG rebuild, which §37f/§37g measured at ~1% of wall,
and **no recomputation at all** — which is why the other-half test passes with
the memo missing every time. §37g called this "the real defect" and ranked it
above everything else; that was wrong, and the table above is why. Partial-
expansion memoisation would buy about one per cent. It is not worth building
until something measures larger.

**What this leaves open.** The test proves the mechanism on a 20,000-node
program whose values are all worth persisting and where nothing is evicted.
The BraTS ladder differs in three ways that the test deliberately does not
model — eviction under real memory pressure, persister shedding under a deep
writer queue, and values below the persist threshold. Those are §37d's three
holes, still unmeasured, and now the test harness exists to model them one at
a time.
