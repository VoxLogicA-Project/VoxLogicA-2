"""engine/concurrency_probe.py: achieved-vs-requested concurrency measurement.

The probe exists so that a disappointing wall-clock can be attributed: a
starving scheduler and a saturated memory system look identical in a timing
table but need opposite fixes (see doc/dev/scaling-test-design.md sec 0-1).
These tests pin the arithmetic and the degenerate cases; whether any particular
engine run saturates is a measurement, not an assertion.
"""

from __future__ import annotations

import threading
import time

import pytest

from voxlogica.engine.concurrency_probe import ConcurrencyProbe


def _probe_over(values: list[int], interval_s: float = 0.005) -> ConcurrencyProbe:
    """Drive a probe over a known sequence, one value per sample tick."""
    it = iter(values)
    last = [0]

    def read() -> int:
        try:
            last[0] = next(it)
        except StopIteration:
            pass  # hold the final value once the sequence is exhausted
        return last[0]

    probe = ConcurrencyProbe(read, interval_s=interval_s)
    probe.start()
    # Wait for at least as many ticks as we have values, with a generous margin
    # so a slow CI host does not truncate the sequence.
    deadline = time.monotonic() + 5.0
    while probe.samples < len(values) and time.monotonic() < deadline:
        time.sleep(interval_s)
    probe.stop()
    return probe


@pytest.mark.unit
def test_mean_is_the_time_average_of_in_flight():
    probe = _probe_over([4] * 20)
    assert probe.samples >= 20
    assert probe.mean_concurrency == pytest.approx(4.0, abs=0.01)


@pytest.mark.unit
def test_peak_is_retained_even_when_brief():
    """A single spike must survive averaging: peak and mean answer different
    questions, and a transient burst is evidence the scheduler *can* dispatch
    wide even if it usually does not."""
    probe = _probe_over([1, 1, 1, 17, 1, 1, 1, 1, 1, 1])
    assert probe.peak_concurrency == 17
    assert probe.mean_concurrency < 5


@pytest.mark.unit
def test_saturation_is_achieved_over_requested():
    probe = _probe_over([6] * 20)
    assert probe.saturation(12) == pytest.approx(0.5, abs=0.02)
    assert probe.saturation(6) == pytest.approx(1.0, abs=0.02)


@pytest.mark.unit
def test_starving_scheduler_is_distinguishable_from_a_busy_one():
    """The whole point of the probe: same requested concurrency, opposite
    diagnosis."""
    starving = _probe_over([1] * 20)
    busy = _probe_over([16] * 20)
    assert starving.saturation(16) < 0.2
    assert busy.saturation(16) > 0.8


@pytest.mark.unit
def test_no_samples_reports_zero_rather_than_dividing_by_zero():
    """A run shorter than one sample interval must not fabricate a value: a
    made-up number here is indistinguishable downstream from a measured one."""
    probe = ConcurrencyProbe(lambda: 5, interval_s=60.0)
    probe.start()
    probe.stop()
    assert probe.samples == 0
    assert probe.mean_concurrency == 0.0
    assert probe.peak_concurrency == 0
    assert probe.saturation(8) == 0.0


@pytest.mark.unit
def test_saturation_guards_nonpositive_max_concurrency():
    probe = _probe_over([3] * 10)
    assert probe.saturation(0) == 0.0
    assert probe.saturation(-1) == 0.0


@pytest.mark.unit
def test_reader_exceptions_never_abort_the_run():
    """Measuring must not be able to break the thing being measured."""
    calls = [0]

    def flaky() -> int:
        calls[0] += 1
        if calls[0] % 2:
            raise RuntimeError("transient")
        return 8

    probe = ConcurrencyProbe(flaky, interval_s=0.005)
    probe.start()
    deadline = time.monotonic() + 5.0
    while probe.samples < 5 and time.monotonic() < deadline:
        time.sleep(0.005)
    probe.stop()
    assert probe.samples >= 5
    assert probe.mean_concurrency == pytest.approx(8.0, abs=0.01)


@pytest.mark.unit
def test_stop_is_idempotent_and_joins_the_thread():
    probe = ConcurrencyProbe(lambda: 1, interval_s=0.005)
    probe.start()
    time.sleep(0.02)
    probe.stop()
    probe.stop()  # must not raise
    live = [t.name for t in threading.enumerate()]
    assert "voxlogica-concurrency-probe" not in live
