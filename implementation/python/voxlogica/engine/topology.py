"""Default worker-pool sizing, corrected for hybrid P/E-core CPUs.

``os.cpu_count()`` treats every logical CPU as equal. On a hybrid Intel
chip (P-cores + E-cores) it isn't: measured directly on fmt-5000 (8 P-cores
@5.8GHz + 16 E-cores @4.6GHz, no SMT), an E-core delivers ~0.70x a P-core's
throughput on the same ITK kernels this engine dispatches. Neither extreme
is right, and both were tried before this settled: using every logical CPU
(24) is 14% slower than the optimum because useful concurrency saturates in
the memory system first, while using only P-cores (8) is 33% slower --
E-cores contribute real throughput, just not free throughput. The measured
optimum is P-cores plus half the E-cores; see ``default_concurrency``'s
docstring for the sweep, and doc/dev/free-threaded-handover.md for the
supporting STREAM ceiling, per-core-type pinning, and perf-stat breakdown.

Every mode here is a heuristic informed by ONE host and ONE workload's
saturation point, not a general law: a memory-light workload, a box with
more DRAM channels, or a non-hybrid CPU may saturate somewhere else or not
at all. --threads N (engine/strategy.py's existing flag) always wins
outright; --threads-auto is the escape hatch for the heuristic itself.
"""

from __future__ import annotations

import os
import sys

_CPU_CORE_LIST_PATH = "/sys/devices/cpu_core/cpus"
_hint_printed = False  # module-level: print the calibrate hint at most once per process


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


def default_concurrency(mode: str = "balanced") -> int:
    """Pick a default worker-pool size.

    ``mode``:
    - ``"balanced"`` (default): P-cores plus HALF the E-cores. E-cores do
      contribute real throughput (see the sweep below) but at rising cost,
      and useful concurrency for an ITK volume workload saturates well before
      every logical CPU is busy; half the E-cores lands in the flat bottom of
      the measured curve.
    - ``"p-cores"``: P-cores only. Minimises CPU-seconds and RSS at a real
      wall-clock cost -- the right choice on a shared box, the wrong one on a
      dedicated machine where latency is what matters.
    - ``"logical"``: always ``os.cpu_count()``, the pre-existing behaviour.

    Measured, TACAS'19 BraTS benchmark, 40 cases, fmt-5000 (8 P + 16 E):

    ======= ======== ========= =======
    threads wall (s) CPU-sec   RSS
    ======= ======== ========= =======
    8       9.12     58.7      1.96 GB   <- "p-cores"
    12      7.50     69.0      2.61 GB
    14      6.90     75.5      2.89 GB
    16      6.85     84.4      3.12 GB   <- "balanced", the optimum
    18      7.06     98.0      3.28 GB
    20      7.26     112.5     3.69 GB
    24      7.84     144.9     4.38 GB   <- "logical"
    ======= ======== ========= =======

    Same ordering at full 259-case scale (16 threads 38.61s/542 CPU-s vs.
    24 threads 47.67s/1010 CPU-s vs. 8 threads 52.45s/368 CPU-s). The curve
    is flat between 14 and 18 (within 3%), so the exact split matters less
    than not landing at either extreme.

    Why not simply "all cores": past ~16 the memory system, not the
    scheduler, is the limit -- confirmed by decoupling the loop-admission
    window from the worker count (``VOXLOGICA_LOOP_WINDOW``, which the engine
    now honours below the worker count for exactly this experiment). Running
    24 workers with an 8-body window cuts the working set and recovers some
    of the loss (8.10s -> 7.37s) but under-feeds the pool (1250% CPU: the
    open bodies don't expose enough ready nodes for 24 workers) and never
    reaches the 16-thread number. Adding workers past the saturation point
    costs CPU and memory without buying wall-clock, whatever the window.

    HEURISTIC, NOT A LAW: fitted to one host's saturation point on one
    workload's memory-access pattern. A memory-light workload, more DRAM
    channels, or a non-hybrid CPU will saturate somewhere else. ``--threads N``
    remains the correct answer for anyone who has measured their own case.

    A per-machine measurement beats this heuristic when one exists: if
    ``voxlogica calibrate`` (``engine/calibration.py``) has run on this exact
    host before, its cached result is used instead of the table above --
    checked first, before any of the modes below. That cache lookup is cheap
    (one small JSON file, no SimpleITK import) so it costs nothing on the
    common path where no calibration exists yet; in that case a one-line
    hint pointing at the subcommand is printed once per process, not on
    every engine construction.
    """
    cached = _cached_threads()
    if cached is not None:
        return cached

    logical = os.cpu_count() or 8
    env_override = os.environ.get("VOXLOGICA_THREADS_AUTO", "").strip().lower()
    effective_mode = env_override or mode
    if effective_mode == "logical":
        return logical
    p_cores = _count_cpu_list(_CPU_CORE_LIST_PATH)
    if not p_cores:
        return logical  # no hybrid split exposed: nothing to be clever about

    _print_calibrate_hint()
    if effective_mode == "p-cores":
        return p_cores
    e_cores = max(0, logical - p_cores)
    return p_cores + e_cores // 2


def _cached_threads() -> int | None:
    """Cheap lookup only -- never imports calibration's heavier SimpleITK
    dependency, which is only needed to RUN a sweep, not to read its cache."""
    try:
        from voxlogica.engine.calibration import load_cached_threads
        return load_cached_threads()
    except Exception:
        # A corrupt cache file, a missing cache dir, or any other calibration
        # -side surprise must never break the (already-working) heuristic
        # fallback -- this lookup is a pure optimization.
        return None


def _print_calibrate_hint() -> None:
    global _hint_printed
    if _hint_printed:
        return
    _hint_printed = True
    print(
        "[voxlogica] using a heuristic thread-count default for this hybrid "
        "P/E CPU -- run 'voxlogica calibrate' once to measure the actual "
        "optimum for this machine instead of guessing.",
        file=sys.stderr,
    )
