#!/usr/bin/env python3
"""Summarise a run of tools/perf/suite.py.

    python3 tools/perf/report.py RESULTS_DIR [--max-foreign 100]

Prints a Markdown table, one row per (experiment, configuration), and writes
``summary.json`` beside the runs. Wall time is given as median and range over
the repetitions, never as a mean alone: a bimodal configuration shows up as a
wide range instead of disappearing into an average.

The report also states what makes a row untrustworthy, rather than leaving it
to the reader:

  * goals that differ between runs of the same program (only runs that went
    to the end are compared; an interrupted run is expected to differ);
  * a run that exited non-zero, other than the deliberately interrupted one;
  * a run during which other users used more than ``--max-foreign`` percent
    of one core at some point.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("results", type=Path)
    p.add_argument("--max-foreign", type=float, default=100.0)
    a = p.parse_args(argv[1:])

    meta = json.loads((a.results / "meta.json").read_text())
    runs = [json.loads(line) for line in (a.results / "runs.jsonl").read_text().splitlines() if line]

    problems: list[str] = []
    complete = [r for r in runs if r["ended_by"] == "exit" and r["exit_code"] == 0]
    digests = {r["goals_digest"] for r in complete if r["goals_digest"]}
    if len(digests) > 1:
        by = {}
        for r in complete:
            by.setdefault(r["goals_digest"], []).append(r["run_id"])
        problems.append("goals differ between complete runs: " + "; ".join(
            f"{d}: {', '.join(ids)}" for d, ids in by.items()))
    for r in runs:
        if r["exit_code"] != 0 and r["ended_by"] != "sigint":
            problems.append(f"{r['run_id']} exited with {r['exit_code']}")
        if r["ended_by"] == "exit" and r["config"] == "interrupted":
            problems.append(f"{r['run_id']} finished before it could be interrupted")
        if (r.get("foreign_cpu_max_pct") or 0) > a.max_foreign:
            problems.append(f"{r['run_id']} ran under foreign load "
                            f"(max {r['foreign_cpu_max_pct']}% of a core)")
        if complete and r in complete and r["goals"] == 0:
            problems.append(f"{r['run_id']} printed no goals")

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in runs:
        if r["experiment"] == "warmup":
            continue
        groups.setdefault((r["experiment"], r["config"]), []).append(r)

    rows = []
    for (experiment, config), rs in groups.items():
        walls = [r["wall_s"] for r in rs]
        rows.append({
            "experiment": experiment, "config": config, "n": len(rs),
            "threads": rs[0]["threads"],
            "wall_median_s": _median(walls), "wall_min_s": min(walls), "wall_max_s": max(walls),
            "cpu_per_wall": _median([r["cpu_per_wall"] for r in rs]),
            "max_rss_mb": _median([r["max_rss_mb"] for r in rs]),
            "kernels_executed": _median([r.get("kernels_executed") for r in rs]),
            "saturation": _median([r.get("saturation") for r in rs]),
            "cones_dispatched": _median([r.get("cones_dispatched") for r in rs]),
            "ops_fused": _median([r.get("ops_fused") for r in rs]),
            "foreign_cpu_max_pct": max((r.get("foreign_cpu_max_pct") or 0) for r in rs),
        })

    engine = meta["engine"]
    print(f"# Performance run — {meta['host']}, {meta['started_at']}\n")
    print(f"- engine: {engine['branch']} {engine['describe']}"
          f"{' **(dirty tree)**' if engine['dirty'] else ''}")
    interp = engine.get("interpreter", {})
    print(f"- interpreter: Python {interp.get('python')} "
          f"free-threaded={interp.get('free_threaded_build')}; "
          + ", ".join(f"{k} {v}" for k, v in (interp.get("packages") or {}).items()))
    m = meta["machine"]
    print(f"- machine: {m['cpu_model']}, {m['logical_cpus']} CPUs, {m['mem_total_gb']} GB; "
          f"work dir on {meta['work_dir_fs']}")
    print(f"- program: {meta['program']['source']} {meta['program']['overrides']} "
          f"(sha256 {meta['program']['sha256'][:12]})")
    print(f"- settings: {json.dumps(meta['settings'])}\n")
    print("| experiment | config | n | thr | wall median (min–max) s | cpu/wall | RSS MB "
          "| kernels | saturation | cones | fused | foreign max % |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['experiment']} | {r['config']} | {r['n']} | {r['threads']} | "
              f"{_fmt(r['wall_median_s'], 2)} ({_fmt(r['wall_min_s'], 2)}–{_fmt(r['wall_max_s'], 2)}) | "
              f"{_fmt(r['cpu_per_wall'], 2)} | {_fmt(r['max_rss_mb'], 0)} | "
              f"{_fmt(r['kernels_executed'], 0)} | {_fmt(r['saturation'], 3)} | "
              f"{_fmt(r['cones_dispatched'], 0)} | {_fmt(r['ops_fused'], 0)} | "
              f"{_fmt(r['foreign_cpu_max_pct'], 0)} |")
    print("\n## Problems\n")
    print("\n".join(f"- {x}" for x in problems) if problems else "- none")
    (a.results / "summary.json").write_text(json.dumps(
        {"meta": meta, "rows": rows, "problems": problems}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
