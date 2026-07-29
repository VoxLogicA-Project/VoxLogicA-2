"""Default worker-pool sizing, corrected for hybrid P/E-core CPUs.

``os.cpu_count()`` treats every logical CPU as equal. On a hybrid Intel
chip (P-cores + E-cores) it isn't: measured directly on fmt-5000 (8 P-cores
@5.8GHz + 16 E-cores @4.6GHz, no SMT), an E-core delivers ~0.70x a P-core's
throughput on the same ITK kernels this engine dispatches, and the box's
actual DRAM bandwidth ceiling (STREAM triad) is reached at 8 threads and
DECLINES past it. Consequently the real TACAS'19 BraTS benchmark run at
--threads=24 (this host's logical CPU count) is both slower AND ~2x the
CPU-seconds of --threads=16 -- more threads made it worse, not just
wasteful. See doc/dev/free-threaded-handover.md's bandwidth section for the
full measurement (STREAM sweep, perf stat cache-miss/IPC breakdown, thread
sweep on the real benchmark at both 40-case and full 259-case scale).

The default this module picks -- P-core count, when the kernel exposes the
split -- is a heuristic informed by ONE host and ONE workload's bandwidth
saturation point, not a general law: a memory-light workload, a box with
more DRAM channels, or a non-hybrid CPU may saturate at a different point
or not at all. --threads N (engine/strategy.py's existing flag) always
wins outright; --threads-auto is the escape hatch for the heuristic itself.
"""

from __future__ import annotations

import os

_CPU_CORE_LIST_PATH = "/sys/devices/cpu_core/cpus"


def _count_cpu_list(path: str) -> int | None:
    """Parse a sysfs CPU list ("0-7" or "0-3,8-11") into a count, or None."""
    try:
        with open(path) as f:
            spec = f.read().strip()
    except OSError:
        return None
    if not spec:
        return None
    total = 0
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            total += int(hi) - int(lo) + 1
        else:
            total += 1
    return total or None


def default_concurrency(mode: str = "p-cores") -> int:
    """Pick a default worker-pool size.

    ``mode``:
    - ``"p-cores"`` (default): on a Linux host that exposes the Intel hybrid
      P/E split (``/sys/devices/cpu_core/cpus``), return the P-core count.
      Anywhere else (macOS, non-hybrid CPU, cgroup without that sysfs path),
      silently fall back to ``os.cpu_count()`` -- this mode never raises and
      never returns something worse than the plain logical count.
    - ``"logical"``: always ``os.cpu_count()``, the pre-existing behavior.
      Use this to disable the heuristic outright (also settable via the
      ``VOXLOGICA_THREADS_AUTO`` env var, for contexts that construct the
      engine directly without going through the CLI's ``--threads-auto``).
    """
    logical = os.cpu_count() or 8
    env_override = os.environ.get("VOXLOGICA_THREADS_AUTO", "").strip().lower()
    effective_mode = env_override or mode
    if effective_mode == "logical":
        return logical
    p_cores = _count_cpu_list(_CPU_CORE_LIST_PATH)
    return p_cores or logical
