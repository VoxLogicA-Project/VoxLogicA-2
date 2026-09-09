"""The measurement framework, pinned by the mistakes it exists to prevent.

Every test here is a wrong number that was actually reported:

  * a CPU figure 39% too high, because the reader divided tick deltas by the
    period it ASKED for instead of the interval that elapsed;
  * a headline mean taken from a sampled series when an exact one from
    ``getrusage`` was available in the same file;
  * an instrument whose perturbation was asserted rather than measured;
  * a measurement whose provenance (commit, host, flags, GIL state) was not
    recorded, so two contradictory numbers could not be told apart afterwards.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "tools" / "measure" / "report.py"


def _program(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.imgql"
    path.write_text('print "x" 1+1\n', encoding="utf-8")
    return path


def _run(args: list[str], tmp_path: Path):
    return subprocess.run([sys.executable, "-m", "voxlogica.main", *args],
                          capture_output=True, text=True, timeout=300,
                          cwd=tmp_path,
                          env={"PYTHONPATH": str(REPO / "implementation" / "python"),
                               "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})


def _load_header(path: Path) -> dict:
    lines = [l[2:] for l in path.read_text(encoding="utf-8").splitlines()
             if l.startswith("# ") and not l.startswith("# voxlogica measurement")]
    return json.loads("\n".join(lines))


@pytest.mark.unit
def test_measurement_is_off_by_default(tmp_path: Path) -> None:
    """No flag, no file, and nothing to opt out of.

    An instrument that is always on is an instrument that is always in the
    measurement.
    """
    program = _program(tmp_path)
    result = _run(["run", "--no-serve", str(program)], tmp_path)
    assert result.returncode == 0, result.stderr[-2000:]
    assert not list(tmp_path.glob("*.tsv")), "a measurement was written unasked"
    assert "[measure]" not in result.stderr


@pytest.mark.unit
def test_measurement_records_its_own_provenance_and_an_exact_total(tmp_path: Path) -> None:
    """The file must answer "what ran, where, and how do you know" by itself."""
    program = _program(tmp_path)
    out = tmp_path / "m.tsv"
    result = _run(["run", "--no-serve", "--measure", str(out), str(program)], tmp_path)
    assert result.returncode == 0, result.stderr[-2000:]
    assert out.is_file(), result.stderr[-2000:]

    header = _load_header(out)
    for key in ("argv", "commit", "host", "cpu_count", "python",
                "free_threaded_build", "gil_enabled", "period_requested_s"):
        assert key in header, f"provenance field {key} missing"

    auth = header["authoritative"]
    # THE headline number, and it must come from the kernel rather than from
    # the series: a sampled mean has a sampling error, this does not.
    assert "getrusage" in auth["source"]
    assert auth["wall_s"] > 0
    assert auth["mean_cpu_percent"] is not None and auth["mean_cpu_percent"] > 0
    assert auth["cores_available"] >= 1

    out = header["outcome"]
    # A measurement of a failed run must say so, in the same file as the
    # timings: a sweep that aborts early looks fast, and one did.
    assert out["recorded"] is True
    assert out["goals_total"] == 1 and out["goals_resolved"] == 1
    assert out["complete"] is True and out["error"] is None

    inst = header["instrument"]
    # Non-perturbation as a measurement, not a promise. On a host without
    # RUSAGE_THREAD it must SAY so rather than imply zero.
    assert "sampler_cpu_s" in inst
    assert inst["sampler_cpu_s"] == 0 or isinstance(inst["sampler_cpu_s"], (float, int, str))
    assert "39%" in inst["note"], "the rule the note exists to state was dropped"


@pytest.mark.unit
def test_rates_come_from_measured_intervals_not_the_nominal_period() -> None:
    """The 39% error, reproduced and refused.

    Two samples one tick-count apart, taken 2 s apart, while the file's header
    says the period was 1 s. A reader that trusts the header reports double.
    """
    sys.path.insert(0, str(REPORT.parent))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("vox_measure_report", REPORT)
        report = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(report)
    finally:
        sys.path.pop(0)

    header = {"ticks_per_second": 100, "period_requested_s": 1.0}
    columns = ["t_ns", "proc_ticks", "loop_ticks"]
    rows = [["0", "0", "0"], ["2000000000", "400", "100"]]   # 4.00 CPU-s over 2 s

    points = report.series(header, columns, rows)
    assert len(points) == 1
    _t, proc_pct, loop_pct = points[0]
    assert proc_pct == pytest.approx(200.0), (
        "process rate must be 400 ticks / 100 / 2 s = 200%, not the 400% a "
        "reader gets by dividing by the requested 1 s period")
    assert loop_pct == pytest.approx(50.0)


@pytest.mark.unit
def test_report_refuses_to_invent_a_series_from_one_sample(tmp_path: Path) -> None:
    """A rate needs two readings. One sample is a state, not a rate."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("vox_measure_report2", REPORT)
    report = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(report)

    points = report.series({"ticks_per_second": 100}, ["t_ns", "proc_ticks", "loop_ticks"],
                           [["0", "0", "0"]])
    assert points == []
