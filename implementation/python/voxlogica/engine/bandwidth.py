"""Memory-bandwidth telemetry: what the run achieves, against what the machine can do.

Efficiency claims on this project must be backed by evidence (AGENT.md §6.5): a
run that is not at 100% must PROVE it is bandwidth-bound rather than assert it.
That needs two numbers, and neither may perturb the run that is being measured.

1. ACHIEVED TRAFFIC — bytes the engine moves through values (each kernel's
   inputs read + its output written), accumulated as two integer adds per
   completion on work the scheduler already does. No timers around kernels, no
   sampling thread, no counters read per operation. It is a LOWER BOUND on DRAM
   traffic: a kernel may touch its input several times internally, and caches
   absorb part of what it does touch. Treat it as "at least this much", which is
   exactly the direction that makes a bandwidth-bound claim safe — if the lower
   bound is already at the machine's ceiling, the case is proved.

2. THE CEILING — measured on this machine, once, with a large-block copy
   (the STREAM "copy" kernel in miniature). Reported as bytes/s of traffic
   (read + write), so it is directly comparable to (1). Runs in ~0.2 s at
   startup and is cached for the process; on a run of any interesting size that
   is unmeasurable overhead, and it beats a hardcoded number that would be wrong
   on every machine but the one it was written on.

Utilization is (1)/(2). Sustained utilization near 1.0 with idle cores is the
factual signature of a bandwidth-bound workload; low utilization with idle cores
means the engine is stalling on something else and has a bug to fix.
"""

from __future__ import annotations

import os
import time

_CEILING_BYTES_PER_S: float | None = None

#: Big enough to defeat any last-level cache on a workstation, small enough that
#: the probe is imperceptible next to a real run.
_PROBE_BYTES = 256 * 1024 * 1024
_PROBE_REPS = 3


def measure_ceiling_bytes_per_s(threads: int = 0) -> float:
    """Measure this machine's SUSTAINED copy bandwidth (read+write bytes/s).

    THREADED ON PURPOSE. A single-threaded copy measures one core's share of the
    memory system, not the machine's: measured here 14.6 GB/s single-threaded
    against ~69 GB/s with enough threads — a 4.7x error, and every utilisation
    figure computed against the single-threaded number was wrong by that factor.
    The ceiling is what ALL the workers together can pull, so the probe must use
    the same worker count the engine will run with.

    numpy's copy releases the GIL, so threads here overlap for real. Cached for
    the process: ~0.2 s once, unmeasurable against any real run.
    """
    global _CEILING_BYTES_PER_S
    if _CEILING_BYTES_PER_S is not None:
        return _CEILING_BYTES_PER_S
    try:
        import numpy as np
        from concurrent.futures import ThreadPoolExecutor

        workers = max(1, threads or (os.cpu_count() or 8))
        # Per-thread buffers, each far beyond any last-level cache, so every
        # access reaches DRAM and threads do not share cache lines.
        per_thread = max(1, _PROBE_BYTES // workers // 8)
        pairs = [(np.ones(per_thread, dtype=np.float64), np.empty(per_thread, dtype=np.float64))
                 for _ in range(workers)]
        for src, dst in pairs:
            dst[:] = src            # first touch: page faults, not memory speed

        def copy(pair):
            src, dst = pair
            dst[:] = src

        best = 0.0
        moved = sum(2 * src.nbytes for src, _ in pairs)   # one read + one write each
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in range(_PROBE_REPS):
                started = time.perf_counter()
                list(pool.map(copy, pairs))
                elapsed = time.perf_counter() - started
                if elapsed > 0:
                    best = max(best, moved / elapsed)
        _CEILING_BYTES_PER_S = best or float("nan")
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        _CEILING_BYTES_PER_S = float("nan")
    return _CEILING_BYTES_PER_S


class BandwidthMeter:
    """Rate of engine-visible traffic between samples. All arithmetic, no probing."""

    def __init__(self) -> None:
        self.bytes_moved = 0          # cumulative, updated by the engine on completion
        self._last_bytes = 0
        self._last_time = time.perf_counter()

    def add(self, nbytes: int) -> None:
        self.bytes_moved += nbytes

    def sample(self, threads: int = 0) -> tuple[float, float]:
        """Return (bytes/s since the last sample, utilization of the ceiling)."""
        now = time.perf_counter()
        elapsed = now - self._last_time
        moved = self.bytes_moved - self._last_bytes
        self._last_time, self._last_bytes = now, self.bytes_moved
        if elapsed <= 0:
            return 0.0, 0.0
        rate = moved / elapsed
        ceiling = measure_ceiling_bytes_per_s(threads)
        return rate, (rate / ceiling if ceiling and ceiling == ceiling else 0.0)
