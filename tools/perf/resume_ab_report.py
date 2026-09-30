#!/usr/bin/env python3
"""Table for resume_ab.sh: the second 35 minutes of each arm, side by side.

usage: resume_ab_report.py <out dir>

For `cont` the second half is t >= 2100 s of its one series; for `kill` it is
the whole series of the resumed process `kill2`. Each row gives the mean rate
over the half, the best 15-minute window inside it, CPU ms per completion and
cores in use over that window, and the recompute share.
"""

import csv
import os
import sys

HALF, WIN, HZ = 2100.0, 900.0, 100.0


def load(path):
    pts = []
    with open(path) as f:
        for r in csv.DictReader(f, delimiter="\t"):
            try:
                pts.append((float(r["t_ns"]) / 1e9, float(r["completed"] or 0),
                            float(r["recomputes"] or 0), float(r["proc_ticks"] or 0)))
            except (KeyError, ValueError):
                pass
    t0 = pts[0][0]
    return [(t - t0, c, rc, pt) for t, c, rc, pt in pts]


def stats(pts):
    span = pts[-1][0] - pts[0][0]
    dc = pts[-1][1] - pts[0][1]
    cpu = 1000.0 * (pts[-1][3] - pts[0][3]) / HZ / dc
    best, j = (0.0, 0.0, 0.0, 0.0), 0
    for i in range(len(pts)):
        while pts[i][0] - pts[j][0] > WIN:
            j += 1
        dt = pts[i][0] - pts[j][0]
        d = pts[i][1] - pts[j][1]
        if dt < 0.9 * WIN or d <= 0:
            continue
        rate = d / dt
        if rate > best[0]:
            ms = 1000.0 * (pts[i][3] - pts[j][3]) / HZ / d
            best = (rate, ms, rate * ms / 1000.0, (pts[i][2] - pts[j][2]) / d)
    return dc, dc / span, cpu, best


def series(out, tag):
    for suffix in (".live.samples.tsv", ".live.report.samples.tsv", ".report.samples.tsv"):
        p = os.path.join(out, tag + suffix)
        if os.path.exists(p):
            return p
    raise SystemExit("no samples for " + tag)


out = sys.argv[1]
cont = [p for p in load(series(out, "cont")) if p[0] >= HALF]
kill = load(series(out, "kill2"))
print("| arm, second 35 min | completions | mean node/s | CPU ms/completion | "
      "best 15-min node/s | CPU ms/completion @best | cores @best | recompute share @best |")
print("|---|---:|---:|---:|---:|---:|---:|---:|")
for name, pts in (("cont: uninterrupted", cont), ("kill: resumed after kill -9", kill)):
    dc, mean, cpu, b = stats(pts)
    print("| %s | %d | %.1f | %.2f | %.1f | %.2f | %.1f | %.1f%% |"
          % (name, dc, mean, cpu, b[0], b[1], b[2], 100 * b[3]))
