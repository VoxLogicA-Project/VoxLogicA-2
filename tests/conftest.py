"""Shared pytest fixtures for VoxLogicA runtime tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
import sys
import tracemalloc

import pytest

from tests.data_registry import CHRIS_T1


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_IMPL = REPO_ROOT / "implementation" / "python"
if str(PYTHON_IMPL) not in sys.path:
    sys.path.insert(0, str(PYTHON_IMPL))


def _ru_maxrss_bytes() -> int:
    import resource

    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return int(value)
    return int(value * 1024.0)


def _record_perf_metric(request: pytest.FixtureRequest, payload: dict[str, float | int | str | bool]) -> None:
    config = request.config
    if not hasattr(config, "_vox_perf_metrics"):
        setattr(config, "_vox_perf_metrics", [])
    metrics: list[dict[str, float | int | str | bool]] = getattr(config, "_vox_perf_metrics")
    metrics.append(payload)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture(autouse=True)
def _collect_perf_telemetry(request: pytest.FixtureRequest):
    if request.node.get_closest_marker("perf") is None:
        yield
        return

    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    rss_before = _ru_maxrss_bytes()
    tracemalloc.start()
    try:
        yield
    finally:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        wall_s = max(0.0, time.perf_counter() - start_wall)
        cpu_s = max(0.0, time.process_time() - start_cpu)
        rss_after = _ru_maxrss_bytes()
        rep = getattr(request.node, "rep_call", None)
        outcome = rep.outcome if rep is not None else "unknown"
        _record_perf_metric(
            request,
            {
                "test_id": request.node.nodeid,
                "outcome": outcome,
                "wall_time_s": wall_s,
                "cpu_time_s": cpu_s,
                "cpu_utilization": (cpu_s / wall_s) if wall_s > 0 else 0.0,
                "ru_maxrss_before_bytes": rss_before,
                "ru_maxrss_after_bytes": rss_after,
                "ru_maxrss_delta_bytes": max(0, rss_after - rss_before),
                "python_heap_current_bytes": int(current_bytes),
                "python_heap_peak_bytes": int(peak_bytes),
            },
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    metrics: list[dict[str, float | int | str | bool]] = getattr(config, "_vox_perf_metrics", [])
    if not metrics:
        return
    report_dir = os.environ.get("VOXLOGICA_PERF_REPORT_DIR")
    if not report_dir:
        return
    output_root = Path(report_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.time(),
        "exitstatus": int(exitstatus),
        "count": len(metrics),
        "tests": metrics,
    }
    (output_root / "perf_test_metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


@pytest.fixture
def reduce_from_text():
    from voxlogica.parser import parse_program_content
    from voxlogica.reducer import reduce_program

    def _reduce(program_text: str):
        program = parse_program_content(program_text)
        return reduce_program(program)

    return _reduce


@pytest.fixture(params=["strict", "dask"])
def strategy_name(request):
    return request.param


@pytest.fixture
def execution_engine():
    from voxlogica.execution import ExecutionEngine

    return ExecutionEngine()


@pytest.fixture
def sample_dataset_file(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "dataset.txt"
    dataset_path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    return dataset_path


@pytest.fixture(scope="session")
def sample_image_path() -> Path:
    if not CHRIS_T1.exists():
        raise FileNotFoundError(f"Missing canonical test image: {CHRIS_T1}")
    return CHRIS_T1
