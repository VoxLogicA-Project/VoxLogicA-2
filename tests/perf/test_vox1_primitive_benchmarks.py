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
PERF_SAMPLE_RUNS = max(2, int(os.environ.get("VOXLOGICA_PERF_SAMPLE_RUNS", "3")))
PERF_WARMUP_RUNS = max(1, int(os.environ.get("VOXLOGICA_PERF_WARMUP_RUNS", "1")))


@dataclass(frozen=True)
class BenchmarkCase:
    name: str


CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("add_mul_chain"),
    BenchmarkCase("distance_and_mask"),
    BenchmarkCase("percentiles"),
    BenchmarkCase("cross_correlation"),
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


def _build_add_mul_workload() -> tuple[list[str], dict[str, object]]:
    factors: tuple[tuple[float, float], ...] = (
        (1.05, 2.10),
        (1.17, 2.25),
        (1.09, 2.35),
        (1.22, 2.45),
        (1.14, 2.55),
        (1.26, 2.20),
        (1.08, 2.30),
        (1.31, 2.40),
        (1.11, 2.50),
        (1.28, 2.60),
        (1.19, 2.15),
        (1.34, 2.28),
        (1.07, 2.38),
        (1.23, 2.48),
        (1.16, 2.58),
        (1.30, 2.68),
    )
    lines = ["let t0 = a"]
    for idx, (mul, div) in enumerate(factors, start=1):
        add_src = "b" if idx % 2 == 1 else "a"
        sub_src = "a" if idx % 3 == 0 else "b"
        lines.append(
            f"let t{idx} = ((t{idx-1} + {add_src}) *. {mul:.3f}) - (({sub_src}) /. {div:.3f})"
        )
    lines.append(f"let r = t{len(factors)}")
    return lines, {
        "repetitions": len(factors),
        "parameters": [
            {"mul": float(mul), "div": float(div)}
            for mul, div in factors
        ],
    }


def _build_distance_workload() -> tuple[list[str], dict[str, object]]:
    thresholds: tuple[int, ...] = (1, 2, 3, 4, 5, 3, 2, 4, 1, 2)
    lines = ["let d0 = mask(dt(ma),ma)"]
    for idx, threshold in enumerate(thresholds, start=1):
        lines.append(f"let m{idx} = {threshold} .<= d{idx-1}")
        lines.append(f"let d{idx} = mask(dt(m{idx}),ma)")
    lines.append(f"let r = d{len(thresholds)}")
    return lines, {
        "repetitions": len(thresholds),
        "parameters": [int(v) for v in thresholds],
    }


def _build_percentiles_workload() -> tuple[list[str], dict[str, object]]:
    corrections: tuple[float, ...] = (0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95)
    lines: list[str] = []
    for idx, corr in enumerate(corrections):
        src = "a" if idx % 2 == 0 else "b"
        mask = "ma" if idx % 3 != 0 else "mb"
        lines.append(f"let p{idx} = percentiles({src},{mask},{corr:.2f})")

    lines.append("let acc0 = p0")
    for idx in range(1, len(corrections)):
        lines.append(f"let acc{idx} = acc{idx-1} + p{idx}")
    lines.append(f"let r = acc{len(corrections)-1} /. {float(len(corrections)):.1f}")
    return lines, {
        "repetitions": len(corrections),
        "parameters": [float(v) for v in corrections],
    }


def _build_crosscorr_workload() -> tuple[list[str], dict[str, object]]:
    sweep: tuple[tuple[str, int, int, str, str], ...] = (
        ("cc0", 1, 8, "a", "b"),
        ("cc1", 2, 12, "b", "a"),
        ("cc2", 3, 16, "cc0", "b"),
        ("cc3", 2, 20, "cc1", "cc0"),
        ("cc4", 1, 24, "cc2", "cc1"),
        ("cc5", 3, 12, "cc3", "cc2"),
        ("cc6", 2, 16, "cc4", "cc5"),
        ("cc7", 1, 20, "cc6", "cc3"),
    )
    lines: list[str] = []
    for name, radius, bins, lhs, rhs in sweep:
        lines.append(
            f"let {name} = crossCorrelation({radius},{lhs},{rhs},tt,min({rhs}),max({rhs}),{bins})"
        )
    lines.extend(
        [
            "let pc0 = percentiles(cc7,ma,0.45)",
            "let pc1 = percentiles(cc6,ma,0.65)",
            "let r = crossCorrelation(2,pc0,pc1,tt,min(pc1),max(pc1),16)",
        ]
    )
    return lines, {
        "repetitions": len(sweep) + 1,
        "parameters": [
            {
                "name": name,
                "radius": int(radius),
                "bins": int(bins),
                "lhs": lhs,
                "rhs": rhs,
            }
            for name, radius, bins, lhs, rhs in sweep
        ],
    }


def _build_program(prelude: str, case: BenchmarkCase) -> tuple[str, dict[str, object]]:
    workload_map = {
        "add_mul_chain": _build_add_mul_workload,
        "distance_and_mask": _build_distance_workload,
        "percentiles": _build_percentiles_workload,
        "cross_correlation": _build_crosscorr_workload,
    }
    builder = workload_map[case.name]
    lines, metadata = builder()
    joined_lines = "\n".join(lines)
    program = (
        f"{prelude}"
        f"{joined_lines}\n"
        'print "res" avg(mask(r,ma),ma)\n'
    )
    return program, metadata


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
    height = 130 + row_h * len(rows)
    left = 260
    bar_max = width - left - 140
    max_time = max(
        [float(row["vox1_median_s"]) for row in rows] + [float(row["vox2_median_s"]) for row in rows] + [1e-9]
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f5f8fd"/>',
        '<text x="28" y="34" font-size="24" font-family="Arial, sans-serif" fill="#12263a">Vox1 Parity Primitive Stress Benchmarks</text>',
        '<text x="28" y="58" font-size="13" font-family="Arial, sans-serif" fill="#486581">Median wall time on repeated parameter sweeps per primitive (higher CPU load, less process-overhead bias).</text>',
    ]
    y = 102
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
        "sample_runs": int(PERF_SAMPLE_RUNS),
        "warmup_runs": int(PERF_WARMUP_RUNS),
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
    return write_deterministic_gray_pair(root, seed=17, shape=(32, 60, 60), spacing=(0.85, 1.10, 1.30))


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
        legacy_text, workload_meta = _build_program(legacy_prelude, case)
        v2_text, _ = _build_program(v2_prelude, case)

        for _ in range(PERF_WARMUP_RUNS):
            _run_legacy(legacy_binary, tmp_path, legacy_text)
            _run_v2(v2_text)

        legacy_samples = [_run_legacy(legacy_binary, tmp_path, legacy_text) for _ in range(PERF_SAMPLE_RUNS)]
        v2_samples = [_run_v2(v2_text) for _ in range(PERF_SAMPLE_RUNS)]

        legacy_median = statistics.median(float(sample["wall_time_s"]) for sample in legacy_samples)
        v2_median = statistics.median(float(sample["wall_time_s"]) for sample in v2_samples)
        rows.append(
            {
                "name": case.name,
                "vox1_median_s": float(legacy_median),
                "vox2_median_s": float(v2_median),
                "speed_ratio": float(legacy_median / max(v2_median, 1e-9)),
                "workload_repetitions": float(int(workload_meta["repetitions"])),
                "workload_parameters": json.dumps(workload_meta["parameters"], sort_keys=True),
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
