from __future__ import annotations

from pathlib import Path
import statistics
import subprocess
import time

import numpy as np
import pytest
import SimpleITK as sitk

from tests._vox1_binary import LEGACY_BIN_ENV, resolve_legacy_binary_path
from voxlogica.execution_strategy.strict import StrictExecutionStrategy
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


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
        "let p = percentiles(c2,ma,0.5)\n"
        f'print "{output_label}" avg(p,ma)\n'
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
        "let p = percentiles(c2,ma,0.5)\n"
        f'print "{output_label}" avg(p,ma)\n'
    )


def _run_legacy(binary: Path, workdir: Path, program_text: str) -> float:
    program_path = workdir / "perf_legacy.imgql"
    program_path.write_text(program_text, encoding="utf-8")
    start = time.perf_counter()
    result = subprocess.run(
        [str(binary), str(program_path)],
        cwd=str(workdir),
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise AssertionError(
            "Legacy perf program failed\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return elapsed


def _run_v2(program_text: str) -> float:
    start = time.perf_counter()
    program = parse_program_content(program_text)
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    elapsed = time.perf_counter() - start
    if not result.success:
        raise AssertionError(f"VoxLogicA-2 perf program failed: {result.failed_operations}")
    return elapsed


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
    gray1_path = root / "gray1.nii.gz"
    gray2_path = root / "gray2.nii.gz"

    rng = np.random.default_rng(13)
    base = rng.normal(loc=90.0, scale=25.0, size=(24, 32, 32)).astype(np.float32)
    second = (np.roll(base, shift=2, axis=0) * 0.82 + 11.0).astype(np.float32)

    img1 = sitk.GetImageFromArray(base, isVector=False)
    img2 = sitk.GetImageFromArray(second, isVector=False)
    img1.SetSpacing((0.9, 1.1, 1.4))
    img2.CopyInformation(img1)

    sitk.WriteImage(img1, str(gray1_path))
    sitk.WriteImage(img2, str(gray2_path))
    return {"gray1": gray1_path, "gray2": gray2_path}


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

    legacy_times = [_run_legacy(legacy_binary, tmp_path, legacy_text) for _ in range(2)]
    v2_times = [_run_v2(v2_text) for _ in range(2)]

    legacy_median = statistics.median(legacy_times)
    v2_median = statistics.median(v2_times)

    assert legacy_median > 0.0
    assert v2_median > 0.0

    chart = tmp_path / "vox1_vs_vox2_perf.svg"
    _render_svg(chart, legacy_median, v2_median)
    assert chart.exists()
