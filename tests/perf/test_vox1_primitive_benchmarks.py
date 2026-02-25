from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    expr: str


CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("add_mul_chain", "((a + b) *. 2) - ((b) /. 2)"),
    BenchmarkCase("distance_and_mask", "mask(dt(ma),ma)"),
    BenchmarkCase("percentiles", "percentiles(a,ma,0.5)"),
    BenchmarkCase("cross_correlation", "crossCorrelation(1,a,b,tt,min(b),max(b),8)"),
)


def _prelude_legacy(inputs: dict[str, Path]) -> str:
    return (
        'import "stdlib.imgql"\n'
        f'load m1 = "{inputs["gray1"]}"\n'
        f'load m2 = "{inputs["gray2"]}"\n'
        "let a = intensity(m1)\n"
        "let b = intensity(m2)\n"
        "let ma = 40 .<= a\n"
        "let mb = 60 .<= b\n"
    )


def _prelude_v2(inputs: dict[str, Path]) -> str:
    return (
        'import "simpleitk"\n'
        f'let m1 = ReadImage("{inputs["gray1"]}")\n'
        f'let m2 = ReadImage("{inputs["gray2"]}")\n'
        "let a = intensity(m1)\n"
        "let b = intensity(m2)\n"
        "let ma = 40 .<= a\n"
        "let mb = 60 .<= b\n"
    )


def _program(prelude: str, expr: str) -> str:
    return (
        f"{prelude}"
        f"let r = {expr}\n"
        "print \"res\" avg(mask(r,ma),ma)\n"
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
    source = workdir / "legacy_primitive_bench.imgql"
    source.write_text(program_text, encoding="utf-8")
    start = time.perf_counter()
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_before = _ru_maxrss_bytes_children()
    result = subprocess.run(
        [str(binary), str(source)],
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
            "Legacy benchmark program failed\n"
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
        raise AssertionError(f"VoxLogicA-2 benchmark failed: {result.failed_operations}")
    return {
        "wall_time_s": elapsed,
        "cpu_time_s": cpu_total,
        "cpu_utilization": (cpu_total / elapsed) if elapsed > 0 else 0.0,
        "ru_maxrss_delta_bytes": float(max(0, rss_after - rss_before)),
        "python_heap_current_bytes": float(int(current_bytes)),
        "python_heap_peak_bytes": float(int(peak_bytes)),
    }


def _render_histogram_svg(path: Path, rows: list[dict[str, float | str]]) -> None:
    width = 960
    row_h = 52
    height = 120 + row_h * len(rows)
    left = 260
    bar_max = width - left - 140
    max_time = max(
        [float(row["vox1_median_s"]) for row in rows] + [float(row["vox2_median_s"]) for row in rows] + [1e-9]
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f5f8fd"/>',
        '<text x="28" y="34" font-size="24" font-family="Arial, sans-serif" fill="#12263a">Vox1 Parity Primitive Benchmarks</text>',
        '<text x="28" y="58" font-size="13" font-family="Arial, sans-serif" fill="#486581">Lower is better. Bars show median runtime per primitive case.</text>',
    ]
    y = 96
    for row in rows:
        name = str(row["name"])
        t1 = float(row["vox1_median_s"])
        t2 = float(row["vox2_median_s"])
        w1 = (t1 / max_time) * bar_max
        w2 = (t2 / max_time) * bar_max
        lines.append(f'<text x="28" y="{y+17}" font-size="13" font-family="Arial, sans-serif" fill="#243b53">{name}</text>')
        lines.append(f'<rect x="{left}" y="{y}" width="{w1:.2f}" height="16" rx="4" fill="#d64545"/>')
        lines.append(f'<rect x="{left}" y="{y+20}" width="{w2:.2f}" height="16" rx="4" fill="#1272cc"/>')
        lines.append(f'<text x="{left+w1+8:.2f}" y="{y+13}" font-size="12" font-family="Arial, sans-serif" fill="#102a43">{t1:.3f}s</text>')
        lines.append(f'<text x="{left+w2+8:.2f}" y="{y+33}" font-size="12" font-family="Arial, sans-serif" fill="#102a43">{t2:.3f}s</text>')
        y += row_h
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_report(rows: list[dict[str, float | str]], svg: Path) -> None:
    report_dir = os.environ.get(PERF_REPORT_DIR_ENV)
    if not report_dir:
        return
    output_root = Path(report_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    out_svg = output_root / "primitive_benchmarks.svg"
    out_svg.write_text(svg.read_text(encoding="utf-8"), encoding="utf-8")
    payload = {
        "cases": rows,
        "generated_at": time.time(),
        "chart_svg": str(out_svg),
    }
    (output_root / "primitive_benchmarks.json").write_text(
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
    root = tmp_path_factory.mktemp("vox1_primitive_perf_inputs")
    return write_deterministic_gray_pair(root, seed=17, shape=(26, 44, 44), spacing=(0.9, 1.1, 1.3))


@pytest.mark.perf
@pytest.mark.slow
def test_vox1_primitive_benchmarks_generate_histogram(
    legacy_binary: Path,
    perf_inputs: dict[str, Path],
    tmp_path: Path,
):
    legacy_prelude = _prelude_legacy(perf_inputs)
    v2_prelude = _prelude_v2(perf_inputs)

    rows: list[dict[str, float | str]] = []
    for case in CASES:
        legacy_text = _program(legacy_prelude, case.expr)
        v2_text = _program(v2_prelude, case.expr)

        _run_legacy(legacy_binary, tmp_path, legacy_text)
        _run_v2(v2_text)
        legacy_samples = [_run_legacy(legacy_binary, tmp_path, legacy_text) for _ in range(2)]
        v2_samples = [_run_v2(v2_text) for _ in range(2)]

        legacy_median = statistics.median(float(sample["wall_time_s"]) for sample in legacy_samples)
        v2_median = statistics.median(float(sample["wall_time_s"]) for sample in v2_samples)
        rows.append(
            {
                "name": case.name,
                "vox1_median_s": float(legacy_median),
                "vox2_median_s": float(v2_median),
                "speed_ratio": float(legacy_median / max(v2_median, 1e-9)),
                "vox1_cpu_median_s": float(
                    statistics.median(float(sample["cpu_time_s"]) for sample in legacy_samples)
                ),
                "vox2_cpu_median_s": float(
                    statistics.median(float(sample["cpu_time_s"]) for sample in v2_samples)
                ),
                "vox1_cpu_utilization_median": float(
                    statistics.median(float(sample["cpu_utilization"]) for sample in legacy_samples)
                ),
                "vox2_cpu_utilization_median": float(
                    statistics.median(float(sample["cpu_utilization"]) for sample in v2_samples)
                ),
                "vox1_ru_maxrss_delta_median_bytes": float(
                    statistics.median(float(sample["ru_maxrss_delta_bytes"]) for sample in legacy_samples)
                ),
                "vox2_ru_maxrss_delta_median_bytes": float(
                    statistics.median(float(sample["ru_maxrss_delta_bytes"]) for sample in v2_samples)
                ),
                "vox2_python_heap_peak_median_bytes": float(
                    statistics.median(float(sample["python_heap_peak_bytes"]) for sample in v2_samples)
                ),
            }
        )

    chart = tmp_path / "primitive_benchmarks.svg"
    _render_histogram_svg(chart, rows)
    assert chart.exists()
    _write_report(rows, chart)
