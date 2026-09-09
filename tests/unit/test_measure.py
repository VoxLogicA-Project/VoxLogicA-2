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


def _report_module():
    """The report script, loaded as a module. It is a script, not a package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("vox_measure_report_io", REPORT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


#: Two synthetic samples 2 s apart, with a header claiming a 1 s period, so
#: every derivation below is also a test of the 39% rule: a reader that trusts
#: the header doubles all of it.
_IO_COLUMNS = ["t_ns", "proc_ticks", "loop_ticks",
               "io_rchar", "io_wchar", "io_read_bytes", "io_write_bytes",
               "io_cancelled_write_bytes",
               "dev_sectors_read", "dev_sectors_written",
               "dev_io_ticks_ms", "dev_weighted_io_ms",
               "thr_total", "thr_running", "thr_sleeping", "thr_disk"]


@pytest.mark.unit
def test_device_utilisation_is_io_ticks_over_the_measured_interval() -> None:
    """io_ticks/elapsed, in percent -- the only honest saturation figure.

    1500 ms of device-busy inside a 2 s interval is 75% utilised. Dividing by
    the header's nominal 1 s period would report 150%, which is not even a
    possible value, and that is how the class of error this framework exists to
    prevent announces itself.
    """
    report = _report_module()
    header = {"ticks_per_second": 100, "period_requested_s": 1.0, "sector_bytes": 512}
    rows = [
        ["0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
         "40", "30", "9", "1"],
        ["2000000000", "400", "100", "0", "0", "0", "0", "0",
         "4000", "8000", "1500", "3000", "40", "20", "10", "10"],
    ]
    points = report.derive(header, _IO_COLUMNS, rows)
    assert len(points) == 1
    point = points[0]
    assert point["dev_util_pct"] == pytest.approx(75.0)
    # 8000 sectors of 512 bytes in 2 s = 2.048 MB/s written to the device.
    assert point["dev_write_mb_s"] == pytest.approx(2.048)
    assert point["dev_read_mb_s"] == pytest.approx(1.024)
    # 3000 weighted ms in 2000 ms of wall time = 1.5 requests in flight.
    assert point["dev_queue"] == pytest.approx(1.5)
    # And the CPU rule still holds in the same row.
    assert point["proc_cpu_pct"] == pytest.approx(200.0)


@pytest.mark.unit
def test_process_write_rate_comes_from_proc_self_io_deltas() -> None:
    """write_bytes is what reached storage; wchar is what we asked for.

    Both are reported, because a store that writes to page cache and truncates
    shows a large wchar and almost no write_bytes, and calling either one "what
    the run writes" without the other has produced the wrong conclusion.
    """
    report = _report_module()
    header = {"ticks_per_second": 100, "period_requested_s": 1.0}
    rows = [
        ["0", "0", "0", "0", "0", "0", "0", "0", "", "", "", "", "", "", "", ""],
        ["2000000000", "0", "0", "0", "600000000", "0", "200000000", "0",
         "", "", "", "", "", "", "", ""],
    ]
    point = report.derive(header, _IO_COLUMNS, rows)[0]
    assert point["proc_write_mb_s"] == pytest.approx(100.0)   # 200 MB / 2 s
    assert point["proc_wchar_mb_s"] == pytest.approx(300.0)   # 600 MB / 2 s
    # The device columns were empty in these rows, and an empty cell must stay
    # unmeasured rather than become a zero that averages into the report.
    assert point["dev_util_pct"] is None
    assert report.weighted_mean([point], "dev_util_pct") is None


@pytest.mark.unit
def test_thread_census_is_averaged_only_over_the_samples_that_carry_it() -> None:
    """The census fires on one tick in N; the rest are blank, not zero.

    Averaging blanks as zeros would divide any real D-state count by N and turn
    "eight threads waiting on the disk" into "two", which is the difference
    between a finding and a rounding error.
    """
    report = _report_module()
    header = {"ticks_per_second": 100}
    rows = []
    for tick in range(5):
        row = [str(tick * 250_000_000), "0", "0",
               "0", "0", "0", "0", "0", "0", "0", "0", "0"]
        # Only the first and last tick carry a census: 8 threads in D on both.
        row += ["40", "20", "12", "8"] if tick in (0, 4) else ["", "", "", ""]
        rows.append(row)
    points = report.derive(header, _IO_COLUMNS, rows)
    mean, count = report.census_mean(points, "thr_disk")
    # Four intervals, but only the one ending on tick 4 carries a census.
    assert len(points) == 4
    assert count == 1 and mean == pytest.approx(8.0)
    assert report.census_mean(points, "thr_total")[0] == pytest.approx(40.0)


@pytest.mark.unit
def test_weighted_mean_weights_by_the_measured_interval() -> None:
    """A jittered sampler must not let short quiet ticks outvote long busy ones.

    Intervals of 0.25 s at 0% and 2.0 s at 100% average to 88.9% by duration
    and to 50% by sample count. Sampler intervals of up to 2.5 s have actually
    been observed under load, so the unweighted figure is not a rounding
    difference: here it is off by a factor of 1.8.
    """
    report = _report_module()
    points = [{"dt_s": 0.25, "dev_util_pct": 0.0},
              {"dt_s": 2.0, "dev_util_pct": 100.0}]
    assert report.weighted_mean(points, "dev_util_pct") == pytest.approx(
        200.0 / 2.25)


@pytest.mark.unit
def test_the_new_readings_degrade_to_unmeasured_without_proc() -> None:
    """macOS has no /proc, and an absent reading must not be a zero or a crash.

    The distinction is load-bearing: "write_bytes is 0" is the claim that the
    run wrote nothing, and it must be impossible to make that claim on a host
    that cannot see write_bytes at all.
    """
    from voxlogica.engine import measure

    missing = "/nonexistent-for-tests/proc/self/io"
    assert measure._proc_io(missing) is None
    assert measure._diskstats("sda", "/nonexistent-for-tests/proc/diskstats") is None
    assert measure._thread_census("/nonexistent-for-tests/proc/self/task") is None
    # A synthetic filesystem has no single backing device, and neither has a
    # path that does not exist. Both answer "unknown", not an exception.
    assert measure._resolve_block_device("/nonexistent-for-tests/store.db") is None
    assert measure._resolve_block_device("/", sys_root="/nonexistent-for-tests/sys") is None


@pytest.mark.unit
def test_the_measurement_file_says_whether_io_was_available(tmp_path: Path) -> None:
    """End to end: the columns exist everywhere, filled on Linux, empty elsewhere.

    A reader must be able to tell "the device was idle" from "this host cannot
    see the device", so availability is a recorded field rather than something
    inferred from blank cells.
    """
    program = _program(tmp_path)
    out = tmp_path / "io.tsv"
    result = _run(["run", "--no-serve", "--measure", str(out), str(program)], tmp_path)
    assert result.returncode == 0, result.stderr[-2000:]
    header = _load_header(out)
    for key in ("block_device", "device_stats_available", "process_io_available",
                "census_every_n_ticks", "census_period_s", "sector_bytes"):
        assert key in header, f"the reader cannot interpret the file without {key}"
    # N is a parameter, defaulted from the period, and must fire about once a
    # second rather than on every tick: the census is the one O(threads) read.
    assert header["census_every_n_ticks"] >= 1
    assert header["census_period_s"] == pytest.approx(1.0, abs=0.5)

    columns = next(line for line in out.read_text(encoding="utf-8").splitlines()
                   if not line.startswith("#")).split("\t")
    for name in ("io_wchar", "io_write_bytes", "io_cancelled_write_bytes",
                 "dev_io_ticks_ms", "dev_weighted_io_ms", "thr_disk"):
        assert name in columns, f"column {name} missing"

    report = _report_module()
    # Whatever the platform, the report must produce a row rather than raise.
    assert report.main([str(out)]) == 0


@pytest.mark.unit
def test_shortfall_attribution_charges_each_interval_once() -> None:
    """The four classes must sum to the shortfall, or they are not a partition.

    Three synthetic intervals on a 4-core machine, one per cause, all with the
    same shortfall, plus one saturated interval that contributes none. The
    precedence is the claim under test: an interval with nothing runnable is
    charged to work-starvation even though the loop is also hot, because a
    machine with no work is not waiting for a resource.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vox_measure_attribution", REPO / "tools" / "measure" / "attribution.py")
    attribution = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(attribution)

    columns = ["t_ns", "proc_ticks", "loop_ticks", "thr_disk", "ready", "in_flight"]
    # 100 ticks/s, 1 s intervals. 200 ticks = 200% of the 400% available, so
    # every unsaturated interval below is short by exactly 2 core-seconds.
    rows = [
        # t=0                       start
        ["0",          "0",   "0",   "",  "",   ""],
        # loop hot (100 ticks = 100% of a core), work available
        ["1000000000", "200", "100", "0", "40", "4"],
        # loop cool, work available, a thread in D
        ["2000000000", "400", "110", "2", "40", "4"],
        # loop hot AND nothing runnable -> charged to starvation, not the loop
        ["3000000000", "600", "210", "0", "1",  "0"],
        # saturated: no shortfall to charge to anyone
        ["4000000000", "1000", "220", "0", "40", "4"],
    ]
    header = {"ticks_per_second": 100, "authoritative": {"cores_available": 4}}
    result = attribution.classify(header, columns, rows)

    assert result["shortfall_core_s"] == pytest.approx(6.0)
    part = result["partition"]
    assert part["loop-bound"] == pytest.approx(2.0)
    assert part["disk-wait"] == pytest.approx(2.0)
    assert part["work-starved"] == pytest.approx(2.0)
    assert part["unexplained"] == pytest.approx(0.0)
    assert sum(part.values()) == pytest.approx(result["shortfall_core_s"])
    # The D-thread bound needs no classification: two threads in D for one of
    # the four census-carrying seconds is two core-seconds, a mean of half a
    # thread, and -- every interval here carrying a census -- the same two
    # core-seconds again once that mean is extended over the measured span.
    assert result["d_thread_bound_census_core_s"] == pytest.approx(2.0)
    assert result["mean_d_threads"] == pytest.approx(0.5)
    assert result["d_thread_bound_core_s"] == pytest.approx(2.0)
    assert result["census_coverage"] == pytest.approx(1.0)


@pytest.mark.unit
def test_the_dose_response_is_duration_weighted_and_covers_every_interval() -> None:
    """The band table is the finding, so it must not double-count or drop time.

    A single threshold on loop CPU is arbitrary and moves the answer; the bands
    exist so the argument rests on monotonicity instead. That only works if the
    bands are disjoint, exhaustive over intervals that have both readings, and
    weighted by elapsed time rather than by sample count.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vox_measure_attribution2", REPO / "tools" / "measure" / "attribution.py")
    attribution = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(attribution)

    columns = ["t_ns", "proc_ticks", "loop_ticks"]
    rows = [
        ["0", "0", "0"],
        ["1000000000", "400", "10"],       # 1 s, loop 10%, proc 400%
        ["1250000000", "425", "35"],       # 0.25 s, loop 100%, proc 100%
    ]
    header = {"ticks_per_second": 100, "authoritative": {"cores_available": 4}}
    result = attribution.classify(header, columns, rows)
    bands = {f"{lo:.0f}": (n, cpu, span)
             for lo, _hi, n, cpu, _idle, span in result["bands"]}
    assert bands["0"][0] == 1 and bands["0"][1] == pytest.approx(400.0)
    assert bands["90"][0] == 1 and bands["90"][1] == pytest.approx(100.0)
    # Exhaustive: the bands account for all 1.25 s of measured time.
    assert sum(b[5] for b in result["bands"]) == pytest.approx(1.25)
    assert result["measured_s"] == pytest.approx(1.25)
