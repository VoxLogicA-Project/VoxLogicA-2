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
      f"current consecutive-clean streak {streak}   best {best}")
print(f"ACCEPTANCE: 20 consecutive clean runs -- "
      f"{'MET' if streak >= 20 else f'not met ({streak}/20)'}")

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
