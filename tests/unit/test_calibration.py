"""engine/calibration.py: fingerprinting, cache round-trip, idle gate, and
candidate-thread-count derivation. The full sweep (run_calibration) needs a
real hybrid CPU and takes real wall time, so it is exercised manually on
fmt-5000 (see doc/dev/free-threaded-handover.md), not here -- these tests
cover the parts that are host-independent and fast."""

from __future__ import annotations

import json

import pytest

from voxlogica.engine.calibration import (
    MachineFingerprint,
    _candidate_thread_counts,
    _count_cpu_list,
    check_machine_idle,
    load_cached_threads,
    _save_calibration,
)


@pytest.mark.unit
def test_fingerprint_is_deterministic():
    a = MachineFingerprint.detect()
    b = MachineFingerprint.detect()
    assert a == b
    assert a.key() == b.key()


@pytest.mark.unit
def test_fingerprint_key_changes_with_any_field():
    a = MachineFingerprint("Model A", 8, 24, 1000, "6.1", freethreaded=False)
    b = MachineFingerprint("Model B", 8, 24, 1000, "6.1", freethreaded=False)
    assert a.key() != b.key()


@pytest.mark.unit
def test_fingerprint_key_distinguishes_gil_from_freethreaded():
    """A calibrated thread count measured under the GIL must NOT be reused on a
    free-threaded interpreter: the GIL caps useful concurrency near half the
    cores regardless of what the memory system could sustain, so the optimum is
    a property of (machine, interpreter). Every other fingerprint field is
    byte-identical across the two builds, so this field is the only thing
    keeping a stale pre-cutover measurement from being silently reused."""
    gil = MachineFingerprint("Same CPU", 8, 24, 1000, "6.1", freethreaded=False)
    ft = MachineFingerprint("Same CPU", 8, 24, 1000, "6.1", freethreaded=True)
    assert gil.key() != ft.key()


@pytest.mark.unit
def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXLOGICA_CACHE_DIR", str(tmp_path))
    fp = MachineFingerprint("Test CPU", 8, 24, 1000, "6.1", freethreaded=False)
    assert load_cached_threads(fp) is None  # nothing cached yet

    _save_calibration(fp, 16, {8: 2.0, 16: 1.0, 24: 1.5})
    assert load_cached_threads(fp) == 16

    # a different fingerprint must not see this machine's cached value
    other = MachineFingerprint("Other CPU", 4, 8, 500, "5.0", freethreaded=False)
    assert load_cached_threads(other) is None


@pytest.mark.unit
def test_cache_survives_multiple_machines(tmp_path, monkeypatch):
    """The cache file holds one entry per fingerprint, not a single slot --
    calibrating on a second host (or after a CPU swap) must not clobber the
    first machine's saved result."""
    monkeypatch.setenv("VOXLOGICA_CACHE_DIR", str(tmp_path))
    fp1 = MachineFingerprint("CPU One", 8, 24, 1000, "6.1", freethreaded=False)
    fp2 = MachineFingerprint("CPU Two", 4, 16, 2000, "6.2", freethreaded=False)
    _save_calibration(fp1, 16, {16: 1.0})
    _save_calibration(fp2, 8, {8: 1.0})
    assert load_cached_threads(fp1) == 16
    assert load_cached_threads(fp2) == 8


@pytest.mark.unit
def test_load_cached_threads_survives_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXLOGICA_CACHE_DIR", str(tmp_path))
    (tmp_path / "thread_calibration.json").write_text("{not valid json")
    assert load_cached_threads(MachineFingerprint.detect()) is None


@pytest.mark.unit
def test_idle_check_returns_a_verdict():
    result = check_machine_idle()
    assert isinstance(result.is_idle, bool)
    assert result.reason  # always explains itself


@pytest.mark.unit
def test_candidate_thread_counts_span_p_to_p_plus_e():
    fp = MachineFingerprint("Hybrid", p_cores=8, logical_cpus=24, total_ram_bytes=0, kernel_release="", freethreaded=False)
    candidates = _candidate_thread_counts(fp)
    assert candidates[0] == 8          # P-cores alone is always a candidate
    assert candidates[-1] == 24        # every logical CPU is always a candidate
    assert candidates == sorted(candidates)
    assert len(set(candidates)) == len(candidates)  # no duplicates


@pytest.mark.unit
def test_candidate_thread_counts_non_hybrid_falls_back():
    fp = MachineFingerprint("Uniform", p_cores=0, logical_cpus=8, total_ram_bytes=0, kernel_release="", freethreaded=False)
    candidates = _candidate_thread_counts(fp)
    assert candidates and all(c > 0 for c in candidates)


@pytest.mark.unit
def test_count_cpu_list_parses_ranges_and_lists(tmp_path):
    p = tmp_path / "cpus"
    p.write_text("0-3,8-11")
    assert _count_cpu_list(str(p)) == 8
    p.write_text("")
    assert _count_cpu_list(str(p)) is None
    assert _count_cpu_list(str(tmp_path / "missing")) is None
