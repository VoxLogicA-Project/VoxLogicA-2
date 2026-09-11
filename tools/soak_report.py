#!/usr/bin/env python3
"""Read the soak ledger and state the stability number, with its caveats.

The number a paper can carry is "N consecutive clean runs across the matrix",
not a mean. This prints that, plus the failure rate per configuration, so a
configuration that fails disproportionately is visible rather than averaged
away.
"""
from __future__ import annotations

import collections
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/soak/ledger.tsv"
rows = [l.split("\t") for l in open(path).read().splitlines()[1:] if l.strip()]
if not rows:
    print("no runs recorded yet")
    raise SystemExit(0)

hdr = ("started program cache threads wall_s exit goals complete "
       "violations details log").split()
R = [dict(zip(hdr, r)) for r in rows]


def clean(r) -> bool:
    return r["exit"] == "0" and r["complete"] == "True" and r["violations"] == "0"


total, ok = len(R), sum(1 for r in R if clean(r))
streak = best = 0
for r in R:
    streak = streak + 1 if clean(r) else 0
    best = max(best, streak)
print(f"runs {total}   clean {ok} ({ok/total:.1%})   "
      f"consecutive-clean RUNS: current {streak}, best {best}")

# THE CRITERION IS ROUNDS, NOT RUNS, and the difference is not pedantic: it
# read "ACCEPTANCE MET, streak 27" while ONE configuration was failing every
# single time it came up. A round is 45 runs and that configuration is 3 of
# them, so a 27-run streak accumulates comfortably between its failures. A
# consecutive-run count measures how spread out the failures are, which is not
# what anyone wants to know.
#
# A ROUND is one full pass over the matrix: every program x cache x thread
# count, each exactly once. Counting them by configuration coverage rather
# than by position, so a partial round at the end of the ledger is not
# mistaken for a clean one.
configs = sorted({(r["program"], r["cache"], r["threads"]) for r in R})
rounds, seen, cur_clean, round_streak, best_round = [], set(), True, 0, 0
for r in R:
    k = (r["program"], r["cache"], r["threads"])
    if k in seen:                       # this run starts a new round
        rounds.append(cur_clean)
        seen, cur_clean = set(), True
    seen.add(k)
    cur_clean = cur_clean and clean(r)
complete_rounds = [c for c in rounds]
for c in complete_rounds:
    round_streak = round_streak + 1 if c else 0
    best_round = max(best_round, round_streak)
print(f"rounds {len(complete_rounds)} over {len(configs)} configurations   "
      f"clean rounds {sum(complete_rounds)}   "
      f"consecutive-clean ROUNDS: current {round_streak}, best {best_round}")
print(f"ACCEPTANCE: 20 consecutive clean ROUNDS -- "
      f"{'MET' if round_streak >= 20 else f'not met ({round_streak}/20)'}")

print("\nby configuration (clean / runs)")
by = collections.defaultdict(lambda: [0, 0])
for r in R:
    k = (r["program"], r["cache"], r["threads"])
    by[k][1] += 1
    by[k][0] += clean(r)
for (prog, cache, t), (c, n) in sorted(by.items()):
    flag = "" if c == n else "   <-- FAILING"
    print(f"  {prog:<44} {cache:<16} t={t:<3} {c:>3}/{n:<3}{flag}")

bad = [r for r in R if not clean(r)]
if bad:
    print(f"\nfailures ({len(bad)}), newest last")
    for r in bad[-12:]:
        print(f"  {r['started']}  {r['program']}  {r['cache']}  t={r['threads']}  "
              f"exit={r['exit']}  goals={r['goals']}  viol={r['violations']}  "
              f"{r['details']}  {r['log']}")
