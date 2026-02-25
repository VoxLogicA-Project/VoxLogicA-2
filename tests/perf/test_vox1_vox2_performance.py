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
PERF_SAMPLE_RUNS = max(2, int(os.environ.get("VOXLOGICA_PERF_SAMPLE_RUNS", "3")))
PERF_WARMUP_RUNS = max(1, int(os.environ.get("VOXLOGICA_PERF_WARMUP_RUNS", "1")))


def _workload_block() -> tuple[str, list[dict[str, int | str]]]:
    sweep: list[dict[str, int | str]] = [
        {"name": "c1", "radius": 1, "bins": 8, "lhs": "a", "rhs": "b"},
        {"name": "c2", "radius": 2, "bins": 12, "lhs": "b", "rhs": "a"},
        {"name": "c3", "radius": 3, "bins": 16, "lhs": "c1", "rhs": "b"},
        {"name": "c4", "radius": 2, "bins": 20, "lhs": "c2", "rhs": "c1"},
        {"name": "c5", "radius": 1, "bins": 24, "lhs": "c3", "rhs": "c2"},
        {"name": "c6", "radius": 3, "bins": 12, "lhs": "c4", "rhs": "c3"},
        {"name": "c7", "radius": 2, "bins": 16, "lhs": "c5", "rhs": "c4"},
        {"name": "c8", "radius": 1, "bins": 20, "lhs": "c6", "rhs": "c7"},
    ]
    lines: list[str] = []
    for item in sweep:
        lines.append(
            "let {name} = crossCorrelation({radius},{lhs},{rhs},tt,min({rhs}),max({rhs}),{bins})".format(
                name=item["name"],
                radius=item["radius"],
                lhs=item["lhs"],
                rhs=item["rhs"],
                bins=item["bins"],
            )
        )
    lines.extend(
        [
            "let p1 = percentiles(c8,ma,0.50)",
            "let p2 = percentiles(c7,ma,0.75)",
            "let p3 = percentiles(c6,ma,0.25)",
            "let c9 = crossCorrelation(2,p1,p2,tt,min(p2),max(p2),16)",
            "let c10 = crossCorrelation(1,c9,p3,tt,min(p3),max(p3),20)",
            "let p4 = percentiles(c10,ma,0.60)",
            "let p5 = percentiles(c10,ma,0.40)",
            "let pf = (p4 + p5) /. 2",
        ]
    )
    return "\n".join(lines) + "\n", sweep


def _legacy_program(inputs: dict[str, Path], output_label: str) -> tuple[str, list[dict[str, int | str]]]:
    workload, sweep = _workload_block()
    program = (
        'import "stdlib.imgql"\n'
        f'load m1 = "{inputs["gray1"]}"\n'
        f'load m2 = "{inputs["gray2"]}"\n'
        "let a = intensity(m1)\n"
        "let b = intensity(m2)\n"
        "let ma = 40 .<= a\n"
        f"{workload}"
        f'print "{output_label}" avg(pf,ma)\n'
    )
    return program, sweep


def _v2_program(inputs: dict[str, Path], output_label: str) -> tuple[str, list[dict[str, int | str]]]:
    workload, sweep = _workload_block()
    program = (
        'import "simpleitk"\n'
        f'let m1 = ReadImage("{inputs["gray1"]}")\n'
        f'let m2 = ReadImage("{inputs["gray2"]}")\n'
        "let a = intensity(m1)\n"
        "let b = intensity(m2)\n"
        "let ma = 40 .<= a\n"
        f"{workload}"
        f'print "{output_label}" avg(pf,ma)\n'
    )
    return program, sweep


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
    width = 680
    height = 300
    padding = 50
    bar_height = 48
    scale = (width - (2 * padding) - 150) / max_value
    vox1_w = vox1_s * scale
    vox2_w = vox2_s * scale
    ratio = vox1_s / max(vox2_s, 1e-9)

    svg = f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">\n  <rect width=\"100%\" height=\"100%\" fill=\"#f6f8fb\"/>\n  <text x=\"{padding}\" y=\"30\" font-size=\"20\" font-family=\"Arial, sans-serif\" fill=\"#102a43\">VoxLogicA-1 vs VoxLogicA-2 Cross-Correlation Stress Performance</text>\n  <text x=\"{padding}\" y=\"52\" font-size=\"13\" font-family=\"Arial, sans-serif\" fill=\"#334e68\">Repeated parameter sweep with many crossCorrelation+percentiles stages (CPU-heavy workload).</text>\n\n  <text x=\"{padding}\" y=\"120\" font-size=\"14\" font-family=\"Arial, sans-serif\" fill=\"#243b53\">VoxLogicA-1</text>\n  <rect x=\"{padding + 120}\" y=\"90\" width=\"{vox1_w:.2f}\" height=\"{bar_height}\" rx=\"6\" fill=\"#d64545\"/>\n  <text x=\"{padding + 132 + vox1_w:.2f}\" y=\"120\" font-size=\"13\" font-family=\"Arial, sans-serif\" fill=\"#102a43\">{vox1_s:.3f}s</text>\n\n  <text x=\"{padding}\" y=\"205\" font-size=\"14\" font-family=\"Arial, sans-serif\" fill=\"#243b53\">VoxLogicA-2</text>\n  <rect x=\"{padding + 120}\" y=\"175\" width=\"{vox2_w:.2f}\" height=\"{bar_height}\" rx=\"6\" fill=\"#1272cc\"/>\n  <text x=\"{padding + 132 + vox2_w:.2f}\" y=\"205\" font-size=\"13\" font-family=\"Arial, sans-serif\" fill=\"#102a43\">{vox2_s:.3f}s</text>\n\n  <text x=\"{padding}\" y=\"266\" font-size=\"14\" font-family=\"Arial, sans-serif\" fill=\"#102a43\">Speed ratio (VoxLogicA-1 / VoxLogicA-2): {ratio:.2f}x</text>\n</svg>\n"""
    path.write_text(svg, encoding="utf-8")


def _write_perf_report(
    chart: Path,
    vox1: dict[str, float],
    vox2: dict[str, float],
    parameter_sweep: list[dict[str, int | str]],
) -> None:
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
        "sample_runs": int(PERF_SAMPLE_RUNS),
        "warmup_runs": int(PERF_WARMUP_RUNS),
        "crosscorr_parameter_sweep": parameter_sweep,
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
    return write_deterministic_gray_pair(root, seed=13, shape=(36, 68, 68), spacing=(0.85, 1.10, 1.35))


@pytest.mark.perf
@pytest.mark.slow
def test_vox1_vs_vox2_perf_comparison_generates_graph(
    legacy_binary: Path,
    perf_inputs: dict[str, Path],
    tmp_path: Path,
):
    legacy_text, parameter_sweep = _legacy_program(perf_inputs, "res")
    v2_text, _ = _v2_program(perf_inputs, "res")

    for _ in range(PERF_WARMUP_RUNS):
        _run_legacy(legacy_binary, tmp_path, legacy_text)
        _run_v2(v2_text)

    legacy_samples = [_run_legacy(legacy_binary, tmp_path, legacy_text) for _ in range(PERF_SAMPLE_RUNS)]
    v2_samples = [_run_v2(v2_text) for _ in range(PERF_SAMPLE_RUNS)]

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
    _write_perf_report(chart, legacy_summary, v2_summary, parameter_sweep)
