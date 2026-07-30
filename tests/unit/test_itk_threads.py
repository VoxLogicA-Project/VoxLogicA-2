"""engine/itk_threads.py: the ITK-vs-engine oversubscription cap.

Only the policy is tested here -- whether a given (workers, cores) pair yields
the intended thread count, and whether an explicit external setting is
respected. Whether the cap actually helps wall-clock is a measurement, not an
assertion: it depends on host, interpreter build, and ITK's own intra-filter
efficiency, and it belongs in a benchmark on a quiet machine (see
engine/calibration.py's docstring on why single-shot timings on a busy or
thermally-throttling host are worse than no measurement at all).
"""

from __future__ import annotations

import os
import sys

import pytest

from voxlogica.engine.itk_threads import (
    _ITK_ENV,
    _OVERRIDE_ENV,
    configure_itk_threads,
    itk_threads_for,
)


@pytest.mark.unit
def test_total_native_threads_never_exceeds_cores():
    """The whole point: workers x itk_threads must stay within the core count,
    since both layers draw from the same CPUs."""
    for cores in (4, 8, 18, 24, 64):
        for workers in range(1, cores + 1):
            itk = itk_threads_for(workers, cores)
            assert itk >= 1
            assert workers * itk <= cores


@pytest.mark.unit
def test_single_worker_gets_the_whole_machine():
    """With one engine worker there is nothing to oversubscribe, so ITK's own
    parallelism is the only parallelism available and must not be throttled."""
    assert itk_threads_for(1, 18) == 18
    assert itk_threads_for(1, 24) == 24


@pytest.mark.unit
def test_reproduces_the_independently_measured_point():
    """doc/dev/free-threaded-handover.md sec 5a A/B'd this knob at 24 engine
    workers on a 24-core host and found 1 best. Any future change to the
    policy must keep agreeing with the one cell that was really measured."""
    assert itk_threads_for(24, 24) == 1


@pytest.mark.unit
def test_more_workers_never_means_more_itk_threads():
    counts = [itk_threads_for(w, 24) for w in range(1, 25)]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.unit
def test_saturated_and_oversubscribed_worker_counts_floor_at_one():
    assert itk_threads_for(18, 18) == 1
    assert itk_threads_for(64, 18) == 1  # more workers than cores
    assert itk_threads_for(0, 18) == 18  # defensive: never divide by zero


@pytest.mark.unit
def test_explicit_itk_env_is_left_alone(monkeypatch):
    """An explicit ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS is a deliberate choice
    by the launcher -- run_iter.sh sets it, and so does any A/B measuring this
    knob. Silently overriding it would make such an experiment measure the
    engine's default instead of the value under test."""
    monkeypatch.setenv(_ITK_ENV, "3")
    assert configure_itk_threads(12) is None
    assert os.environ.get(_ITK_ENV) == "3"


@pytest.mark.unit
def test_override_env_wins_over_the_derived_value(monkeypatch):
    monkeypatch.delenv(_ITK_ENV, raising=False)
    monkeypatch.setenv(_OVERRIDE_ENV, "5")
    assert configure_itk_threads(12) == 5


@pytest.mark.unit
def test_bad_override_falls_back_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.delenv(_ITK_ENV, raising=False)
    monkeypatch.setenv(_OVERRIDE_ENV, "not-a-number")
    applied = configure_itk_threads(1)
    assert applied == itk_threads_for(1)
    assert "WARNING" in capsys.readouterr().err


@pytest.mark.unit
def test_sets_env_so_it_applies_before_simpleitk_initializes(monkeypatch):
    """ITK reads the env var at library init, so setting it is what makes the
    cap effective on the normal path where SimpleITK has not been imported yet
    (primitive namespaces load lazily, after the engine is built)."""
    monkeypatch.delenv(_ITK_ENV, raising=False)
    monkeypatch.delenv(_OVERRIDE_ENV, raising=False)
    applied = configure_itk_threads(4)
    assert applied == itk_threads_for(4)
    assert os.environ.get(_ITK_ENV) == str(applied)


@pytest.mark.unit
def test_calls_the_api_when_simpleitk_is_already_imported(monkeypatch):
    """If ITK has already initialized, the env var is too late and only the
    setter takes effect -- so both paths must fire."""
    monkeypatch.delenv(_ITK_ENV, raising=False)
    monkeypatch.delenv(_OVERRIDE_ENV, raising=False)
    seen: list[int] = []

    class _ProcessObject:
        @staticmethod
        def SetGlobalDefaultNumberOfThreads(n: int) -> None:
            seen.append(n)

    class _FakeSimpleITK:
        ProcessObject = _ProcessObject

    monkeypatch.setitem(sys.modules, "SimpleITK", _FakeSimpleITK)
    applied = configure_itk_threads(18)
    assert seen == [applied]
