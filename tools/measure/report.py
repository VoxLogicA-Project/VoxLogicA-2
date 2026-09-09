#!/usr/bin/env python3
"""Read measurement files and print a table that states its own method.

THE RULE THIS SCRIPT EXISTS TO ENFORCE: a rate is computed from consecutive
sample timestamps, never from the requested period. Dividing by the nominal
period overstated CPU by 39% in the measurement this framework replaced, and
the mistake is invisible in the output unless something refuses to make it.

The headline figure is never a sampled mean. It comes from the file's
`authoritative` block, which the kernel filled in via getrusage at start and
exit and which therefore has no sampling error. The sampled series is used only
for the shape: how much of the run was saturated, and where it was not.

Usage:
    report.py <measurement.tsv> [more.tsv ...]
    report.py --series <measurement.tsv>      # per-sample CPU%, for plotting
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> tuple[dict, list[str], list[list[str]]]:
    header_lines, columns, rows = [], [], []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.startswith("# voxlogica measurement"):
                continue
            if line.startswith("# "):
                header_lines.append(line[2:])
            elif not columns:
                columns = line.split("\t")
            else:
                rows.append(line.split("\t"))
    return json.loads("\n".join(header_lines)), columns, rows


def series(header: dict, columns: list[str], rows: list[list[str]]):
    """(seconds_from_start, process CPU%, loop-thread CPU%) per interval.

    One value per INTERVAL, not per sample: a rate needs two readings, and the
    interval is the measured one.
    """
    ticks = header.get("ticks_per_second", 100)
    t = columns.index("t_ns")
    proc = columns.index("proc_ticks")
    loop = columns.index("loop_ticks")
    out = []
    t0 = int(rows[0][t]) if rows else 0
    for before, after in zip(rows, rows[1:]):
        dt = (int(after[t]) - int(before[t])) / 1e9
        if dt <= 0:
            continue
        p = (int(after[proc]) - int(before[proc])) / ticks / dt * 100.0
        lp = None
        if int(after[loop]) >= 0 and int(before[loop]) >= 0:
            lp = (int(after[loop]) - int(before[loop])) / ticks / dt * 100.0
        out.append(((int(after[t]) - t0) / 1e9, p, lp))
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--series":
        header, columns, rows = load(Path(argv[1]))
        print("t_s\tproc_cpu_pct\tloop_cpu_pct")
        for t, p, lp in series(header, columns, rows):
            print(f"{t:.3f}\t{p:.1f}\t{'' if lp is None else f'{lp:.1f}'}")
        return 0

    print("target: CPU utilisation and wall time of one voxlogica run")
    print("method: getrusage(RUSAGE_SELF) at start and exit for the totals;")
    print("        in-process sampling on its own thread for the shape, rates")
    print("        computed from measured intervals (engine/measure.py)")
    print()
    head = ("file", "wall s", "mean CPU", "of", "peak RSS", "sat>=90%", "loop>=90%",
            "instrument", "commit")
    print("\t".join(head))
    for name in argv:
        path = Path(name)
        try:
            header, columns, rows = load(path)
        except Exception as exc:                            # noqa: BLE001
            print(f"{path.name}\tunreadable: {exc}")
            continue
        auth = header.get("authoritative", {})
        inst = header.get("instrument", {})
        cores = auth.get("cores_available") or 1
        pts = series(header, columns, rows) if len(rows) > 1 else []
        ceiling = 100.0 * cores
        sat = sum(1 for _, p, _ in pts if p >= 0.90 * ceiling)
        loop_hot = sum(1 for _, _, lp in pts if lp is not None and lp >= 90.0)
        share = inst.get("sampler_share_of_run_cpu")
        print("\t".join([
            path.name,
            f"{auth.get('wall_s', 0):.1f}",
            f"{auth.get('mean_cpu_percent')}%",
            f"{ceiling:.0f}%",
            f"{(auth.get('peak_rss_bytes') or 0) / 1e9:.1f} GB",
            f"{sat}/{len(pts)}" if pts else "-",
            f"{loop_hot}/{len(pts)}" if pts else "-",
            (f"{share:.4%}" if isinstance(share, float) else str(share)),
            (header.get("commit") or "")[:8] + ("+dirty" if header.get("working_tree_dirty") else ""),
        ]))
    print()
    print("sat>=90%:  samples at or above 90% of every core busy")
    print("loop>=90%: samples where the event-loop thread alone held ~a full")
    print("           core -- with a low process figure beside it, that is the")
    print("           starvation signature: workers idle, waiting on the loop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
