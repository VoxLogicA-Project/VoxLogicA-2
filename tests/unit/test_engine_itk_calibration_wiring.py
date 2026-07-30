"""ComputationEngine applies a cached ITK-thread calibration ONLY when the
engine's actual worker count matches the worker count it was calibrated at.

This is the wiring half of the ITK calibration feature (engine/calibration.py
has the sweep; engine/itk_threads.py has the mechanical setter). See
manuscripts/engine-scaling-2026-07.md Part I sec 5 for why the match must be
exact: the ITK-thread optimum crosses over with worker count, so a value
measured at 18 workers is not known-good at 8, and applying it there anyway
would silently reintroduce the exact bug (a formula guessing at a value that
needs measurement) this feature replaced.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.core import ComputationEngine


@pytest.mark.unit
def test_cached_itk_threads_applied_at_the_calibrated_worker_count(monkeypatch):
    applied: list[int] = []
    monkeypatch.setattr(
        "voxlogica.engine.core.load_cached_itk_threads",
        lambda workers: 1 if workers == 4 else None,
    )
    monkeypatch.setattr(
        "voxlogica.engine.core.apply_itk_threads",
        lambda n: applied.append(n),
    )
    ComputationEngine(max_concurrency=4)
    assert applied == [1]


@pytest.mark.unit
def test_no_cached_value_at_this_worker_count_leaves_itk_untouched(monkeypatch):
    """The measured-safe fallback: a worker count calibration never swept
    (e.g. an explicit --threads N different from the calibrated winner) must
    NOT apply a value measured for a different worker count."""
    applied: list[int] = []
    monkeypatch.setattr(
        "voxlogica.engine.core.load_cached_itk_threads",
        lambda workers: 1 if workers == 18 else None,
    )
    monkeypatch.setattr(
        "voxlogica.engine.core.apply_itk_threads",
        lambda n: applied.append(n),
    )
    ComputationEngine(max_concurrency=8)  # calibration was for 18, not 8
    assert applied == []


@pytest.mark.unit
def test_no_calibration_at_all_leaves_itk_untouched(monkeypatch):
    applied: list[int] = []
    monkeypatch.setattr(
        "voxlogica.engine.core.load_cached_itk_threads", lambda workers: None)
    monkeypatch.setattr(
        "voxlogica.engine.core.apply_itk_threads", lambda n: applied.append(n))
    ComputationEngine(max_concurrency=4)
    assert applied == []
