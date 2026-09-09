#!/usr/bin/env python3
"""Regenerate doc/dev/measurements/README.md from the JSON reports on disk.

A table that is written by hand goes stale the first time somebody is in a
hurry, and a stale performance table is worse than none: it is quoted. So this
reads every `*.json` report under doc/dev/measurements/ and prints the table,
including the columns that decide whether a change is real:

  wall        what the user waits for
  cpu_s       proportional to ENERGY -- a shorter run bought with more
              CPU-seconds is a heater, not an optimisation
  cpu/compl   CPU-milliseconds per completion: the energy cost of one unit of
              work, which must not rise
  compl/s     what the engine actually retires per second
  kernels     the work done, so a CPU rise from doing more is not read as a win
  recomp      evictions that had to be rebuilt
  ctx sw      involuntary context switches: a parallelisation that trades one
              thread's time for switching shows up here
  goals       a run that did not finish has no timings worth reading
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "doc" / "dev" / "measurements"


def rows():
    for path in sorted(ROOT.glob("*/*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            continue
        if d.get("schema", "").startswith("voxlogica.measurement"):
            yield path, d


def main() -> int:
    out = []
    for path, d in rows():
        t, w = d.get("totals", {}), d.get("work", {})
        th, o = d.get("throughput", {}), d.get("outcome", {})
        env = d.get("environment", {})
        goals = (f"{o.get('goals_resolved')}/{o.get('goals_total')}"
                 if o.get("recorded") else "?")
        out.append((
            path.parent.name, path.stem,
            f"{t.get('wall_seconds', 0):.1f}",
            f"{t.get('mean_cpu_percent')}%",
            f"{t.get('cpu_seconds', 0):.0f}",
            (f"{th.get('cpu_seconds_per_completion_ms'):.2f}"
             if th.get("cpu_seconds_per_completion_ms") else "-"),
            (f"{th.get('completions_per_second'):.0f}"
             if th.get("completions_per_second") else "-"),
            str(w.get("kernels_executed") or "-"),
            str(w.get("recomputes") if w.get("recomputes") is not None else "-"),
            str(t.get("involuntary_switches") or "-"),
            goals,
            (env.get("commit") or "")[:8],
        ))
    head = ("experiment", "run", "wall s", "CPU%", "CPU-s", "CPU ms/compl",
            "compl/s", "kernels", "recomp", "ctx sw", "goals", "commit")
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join("---" for _ in head) + "|")
    for row in out:
        print("| " + " | ".join(row) + " |")
    print()
    print(f"{len(out)} reports under {ROOT.relative_to(ROOT.parents[3])}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
