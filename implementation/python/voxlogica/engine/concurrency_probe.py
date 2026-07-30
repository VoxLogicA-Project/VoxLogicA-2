"""Measure the concurrency the engine ACHIEVED, not the concurrency it was asked for.

``max_concurrency`` is a request. Nothing in the run summary has ever reported
whether the engine actually kept that many kernels in flight, and the absence of
that one number is what made a whole day of scaling work inconclusive: wall-clock
alone cannot distinguish

  - the engine never dispatched W kernels (scheduler- or dependency-limited), from
  - it dispatched W but each was blocked on a lock, from
  - it dispatched W, all were running, and the memory system was the ceiling,

yet those three call for completely different fixes. Every wrong conclusion in
doc/dev/scaling-test-design.md sec 0 is a case of guessing between them from
timings.

So sample ``_in_flight`` on a background thread and integrate it over the run.
``saturation = mean_concurrency / max_concurrency`` is then directly actionable:
near 1.0 means the scheduler is doing its job and any disappointing wall-clock
is the kernels' or the hardware's problem; well below 1.0 means the engine is
starving and no amount of kernel tuning will help.

Sampling is a bare int read at a coarse interval, deliberately: no lock is taken
(a small-int read cannot tear, and an occasional stale sample is irrelevant to a
time-average over thousands of them), so the probe cannot perturb the thing it
measures. That matters -- an instrument that changes the measurement would be
worse than none, which is the same trap the timings fell into.
"""

from __future__ import annotations

import threading
from typing import Callable

# 50 ms: fine enough that a kernel of typical duration (tens of ms upward) is
# not systematically missed, coarse enough that the probe's own cost is far
# below run-to-run noise even on a 20-minute run.
_SAMPLE_INTERVAL_S = 0.05


class ConcurrencyProbe:
    """Time-integrate a live in-flight counter to get mean/peak concurrency."""

    def __init__(self, read_in_flight: Callable[[], int],
                 interval_s: float = _SAMPLE_INTERVAL_S) -> None:
        self._read = read_in_flight
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Written only by the sampler thread, read only after join() -- so the
        # happens-before edge from Thread.join() is all the synchronisation the
        # totals need.
        self._total = 0
        self._samples = 0
        self._peak = 0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="voxlogica-concurrency-probe")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                value = self._read()
            except Exception:  # pragma: no cover - never break a run to measure it
                continue
            self._total += value
            self._samples += 1
            if value > self._peak:
                self._peak = value

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    @property
    def samples(self) -> int:
        return self._samples

    @property
    def mean_concurrency(self) -> float:
        """Average in-flight kernel count over the run.

        Zero samples (a run shorter than one interval) reports 0.0 rather than
        guessing: a fabricated value here would be indistinguishable from a
        measured one downstream.
        """
        if self._samples == 0:
            return 0.0
        return self._total / self._samples

    @property
    def peak_concurrency(self) -> int:
        return self._peak

    def saturation(self, max_concurrency: int) -> float:
        """Achieved / requested concurrency, in [0, 1] for a healthy run."""
        if max_concurrency <= 0 or self._samples == 0:
            return 0.0
        return self.mean_concurrency / max_concurrency
