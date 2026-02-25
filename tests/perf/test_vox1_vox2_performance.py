from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc

import pytest

from tests._vox1_binary import LEGACY_BIN_ENV, resolve_legacy_binary_path
from tests.data_registry import write_deterministic_gray_pair
from voxlogica.execution_strategy.strict import StrictExecutionStrategy
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


PERF_REPORT_DIR_ENV = "VOXLOGICA_PERF_REPORT_DIR"


def _legacy_program(inputs: dict[str, Path], output_label: str) -> str:
    return (
        'import "stdlib.imgql"\n'
        f'load m1 = "{inputs["gray1"]}"\n'
        f'load m2 = "{inputs["gray2"]}"\n'
        "let a = intensity(m1)\n"
        "let b = intensity(m2)\n"
        "let ma = 40 .<= a\n"
        "let c1 = crossCorrelation(2,a,b,tt,min(b),max(b),16)\n"
        "let c2 = crossCorrelation(2,c1,a,tt,min(a),max(a),16)\n"
        "let p1 = percentiles(c2,ma,0.5)\n"
        "let c3 = crossCorrelation(2,p1,b,tt,min(b),max(b),16)\n"
        "let c4 = crossCorrelation(2,c3,p1,tt,min(p1),max(p1),16)\n"
        "let p2 = percentiles(c4,ma,0.75)\n"
        f'print "{output_label}" avg(p2,ma)\n'
    )


def _v2_program(inputs: dict[str, Path], output_label: str) -> str:
    return (
        'import "simpleitk"\n'
        f'let m1 = ReadImage("{inputs["gray1"]}")\n'
        f'let m2 = ReadImage("{inputs["gray2"]}")\n'
        "let a = intensity(m1)\n"
        "let b = intensity(m2)\n"
        "let ma = 40 .<= a\n"
        "let c1 = crossCorrelation(2,a,b,tt,min(b),max(b),16)\n"
        "let c2 = crossCorrelation(2,c1,a,tt,min(a),max(a),16)\n"
        "let p1 = percentiles(c2,ma,0.5)\n"
        "let c3 = crossCorrelation(2,p1,b,tt,min(b),max(b),16)\n"
        "let c4 = crossCorrelation(2,c3,p1,tt,min(p1),max(p1),16)\n"
        "let p2 = percentiles(c4,ma,0.75)\n"
        f'print "{output_label}" avg(p2,ma)\n'
    )


def _ru_maxrss_bytes_children() -> int:
    value = float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    if sys.platform == "darwin":
        return int(value)
    return int(value * 1024.0)


def _ru_maxrss_bytes_self() -> int:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return int(value)
    return int(value * 1024.0)


def _run_legacy(binary: Path, workdir: Path, program_text: str) -> dict[str, float]:
    program_path = workdir / "perf_legacy.imgql"
    program_path.write_text(program_text, encoding="utf-8")
    start = time.perf_counter()
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_before = _ru_maxrss_bytes_children()
    result = subprocess.run(
        [str(binary), str(program_path)],
        cwd=str(workdir),
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_after = _ru_maxrss_bytes_children()
    if result.returncode != 0:
        raise AssertionError(
            "Legacy perf program failed\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    cpu_user = max(0.0, float(cpu_after.ru_utime - cpu_before.ru_utime))
    cpu_sys = max(0.0, float(cpu_after.ru_stime - cpu_before.ru_stime))
    cpu_total = cpu_user + cpu_sys
    return {
        "wall_time_s": elapsed,
        "cpu_time_s": cpu_total,
        "cpu_utilization": (cpu_total / elapsed) if elapsed > 0 else 0.0,
        "ru_maxrss_delta_bytes": float(max(0, rss_after - rss_before)),
    }


def _run_v2(program_text: str) -> dict[str, float]:
    start = time.perf_counter()
    start_cpu = time.process_time()
    rss_before = _ru_maxrss_bytes_self()
    tracemalloc.start()
    program = parse_program_content(program_text)
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - start
    cpu_total = max(0.0, time.process_time() - start_cpu)
    rss_after = _ru_maxrss_bytes_self()
    if not result.success:
        raise AssertionError(f"VoxLogicA-2 perf program failed: {result.failed_operations}")
    return {
        "wall_time_s": elapsed,
        "cpu_time_s": cpu_total,
        "cpu_utilization": (cpu_total / elapsed) if elapsed > 0 else 0.0,
        "ru_maxrss_delta_bytes": float(max(0, rss_after - rss_before)),
        "python_heap_current_bytes": float(int(current_bytes)),
        "python_heap_peak_bytes": float(int(peak_bytes)),
    }


def _render_svg(path: Path, vox1_s: float, vox2_s: float) -> None:
    max_value = max(vox1_s, vox2_s, 1e-9)
    width = 640
    height = 280
    padding = 50
    bar_height = 48
    scale = (width - (2 * padding) - 130) / max_value
    vox1_w = vox1_s * scale
    vox2_w = vox2_s * scale
    ratio = vox1_s / max(vox2_s, 1e-9)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f6f8fb"/>
  <text x="{padding}" y="30" font-size="20" font-family="Arial, sans-serif" fill="#102a43">VoxLogicA-1 vs VoxLogicA-2 Performance</text>
  <text x="{padding}" y="52" font-size="13" font-family="Arial, sans-serif" fill="#334e68">Lower is better; workload is crossCorrelation + percentiles chain.</text>

  <text x="{padding}" y="110" font-size="14" font-family="Arial, sans-serif" fill="#243b53">VoxLogicA-1</text>
  <rect x="{padding + 110}" y="80" width="{vox1_w:.2f}" height="{bar_height}" rx="6" fill="#d64545"/>
  <text x="{padding + 120 + vox1_w:.2f}" y="110" font-size="13" font-family="Arial, sans-serif" fill="#102a43">{vox1_s:.3f}s</text>

  <text x="{padding}" y="190" font-size="14" font-family="Arial, sans-serif" fill="#243b53">VoxLogicA-2</text>
  <rect x="{padding + 110}" y="160" width="{vox2_w:.2f}" height="{bar_height}" rx="6" fill="#1272cc"/>
  <text x="{padding + 120 + vox2_w:.2f}" y="190" font-size="13" font-family="Arial, sans-serif" fill="#102a43">{vox2_s:.3f}s</text>

  <text x="{padding}" y="245" font-size="14" font-family="Arial, sans-serif" fill="#102a43">Speed ratio (VoxLogicA-1 / VoxLogicA-2): {ratio:.2f}x</text>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def _write_perf_report(chart: Path, vox1: dict[str, float], vox2: dict[str, float]) -> None:
    report_dir = os.environ.get(PERF_REPORT_DIR_ENV)
    if not report_dir:
        return
    output_root = Path(report_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    output_chart = output_root / "vox1_vs_vox2_perf.svg"
    output_chart.write_text(chart.read_text(encoding="utf-8"), encoding="utf-8")
    vox1_s = float(vox1["wall_time_s"])
    vox2_s = float(vox2["wall_time_s"])
    payload = {
        "vox1_median_s": float(vox1_s),
        "vox2_median_s": float(vox2_s),
        "speed_ratio": float(vox1_s / max(vox2_s, 1e-9)),
        "vox1_cpu_median_s": float(vox1.get("cpu_time_s", 0.0)),
        "vox2_cpu_median_s": float(vox2.get("cpu_time_s", 0.0)),
        "vox1_cpu_utilization_median": float(vox1.get("cpu_utilization", 0.0)),
        "vox2_cpu_utilization_median": float(vox2.get("cpu_utilization", 0.0)),
        "vox1_ru_maxrss_delta_median_bytes": float(vox1.get("ru_maxrss_delta_bytes", 0.0)),
        "vox2_ru_maxrss_delta_median_bytes": float(vox2.get("ru_maxrss_delta_bytes", 0.0)),
        "vox2_python_heap_peak_median_bytes": float(vox2.get("python_heap_peak_bytes", 0.0)),
        "chart_svg": str(output_chart),
        "generated_at": time.time(),
    }
    (output_root / "vox1_vs_vox2_perf.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


@pytest.fixture(scope="session")
def legacy_binary() -> Path:
    resolved = resolve_legacy_binary_path(auto_download=True)
    if resolved is not None:
        return resolved
    pytest.skip(
        f"Legacy VoxLogicA binary unavailable. Set {LEGACY_BIN_ENV} or allow "
        "release download from GitHub."
    )
    raise AssertionError("unreachable")


@pytest.fixture(scope="session")
def perf_inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("vox1_vox2_perf_inputs")
    return write_deterministic_gray_pair(root, seed=13, shape=(28, 48, 48), spacing=(0.9, 1.1, 1.4))


@pytest.mark.perf
@pytest.mark.slow
def test_vox1_vs_vox2_perf_comparison_generates_graph(
    legacy_binary: Path,
    perf_inputs: dict[str, Path],
    tmp_path: Path,
):
    legacy_text = _legacy_program(perf_inputs, "res")
    v2_text = _v2_program(perf_inputs, "res")

    # Warm-up to reduce first-run variance.
    _run_legacy(legacy_binary, tmp_path, legacy_text)
    _run_v2(v2_text)

    legacy_samples = [_run_legacy(legacy_binary, tmp_path, legacy_text) for _ in range(2)]
    v2_samples = [_run_v2(v2_text) for _ in range(2)]

    legacy_median = statistics.median(float(sample["wall_time_s"]) for sample in legacy_samples)
    v2_median = statistics.median(float(sample["wall_time_s"]) for sample in v2_samples)

    assert legacy_median > 0.0
    assert v2_median > 0.0

    chart = tmp_path / "vox1_vs_vox2_perf.svg"
    _render_svg(chart, legacy_median, v2_median)
    assert chart.exists()
    legacy_summary = {
        "wall_time_s": legacy_median,
        "cpu_time_s": statistics.median(float(sample["cpu_time_s"]) for sample in legacy_samples),
        "cpu_utilization": statistics.median(float(sample["cpu_utilization"]) for sample in legacy_samples),
        "ru_maxrss_delta_bytes": statistics.median(float(sample["ru_maxrss_delta_bytes"]) for sample in legacy_samples),
    }
    v2_summary = {
        "wall_time_s": v2_median,
        "cpu_time_s": statistics.median(float(sample["cpu_time_s"]) for sample in v2_samples),
        "cpu_utilization": statistics.median(float(sample["cpu_utilization"]) for sample in v2_samples),
        "ru_maxrss_delta_bytes": statistics.median(float(sample["ru_maxrss_delta_bytes"]) for sample in v2_samples),
        "python_heap_peak_bytes": statistics.median(float(sample["python_heap_peak_bytes"]) for sample in v2_samples),
    }
    _write_perf_report(chart, legacy_summary, v2_summary)
