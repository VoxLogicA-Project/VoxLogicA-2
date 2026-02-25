from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

import numpy as np
import pytest
import SimpleITK as sitk

from tests._vox1_binary import LEGACY_BIN_ENV, resolve_legacy_binary_path
from voxlogica.execution_strategy.strict import StrictExecutionStrategy
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


@dataclass(frozen=True)
class ParityCase:
    name: str
    kind: str
    domain: str
    legacy_expr: str
    v2_expr: str
    atol: float = 1e-5


SCALAR_CASES: tuple[ParityCase, ...] = (
    ParityCase("core_div", "scalar", "scalar", "8 ./. 2", "8 ./. 2"),
    ParityCase("core_mul", "scalar", "scalar", "3 .*. 4", "3 .*. 4"),
    ParityCase("core_add", "scalar", "scalar", "2 .+. 3", "2 .+. 3"),
    ParityCase("core_sub", "scalar", "scalar", "7 .-. 5", "7 .-. 5"),
    ParityCase("core_and", "scalar", "scalar", "true .&. false", "true .&. false"),
    ParityCase("core_or", "scalar", "scalar", "true .|. false", "true .|. false"),
    ParityCase("core_not", "scalar", "scalar", "!. true", "!.(true)"),
    ParityCase("core_eq", "scalar", "scalar", "5 .=. 5", "5 .=. 5"),
    ParityCase("core_leq", "scalar", "scalar", "4 .<=. 5", "4 .<=. 5"),
    ParityCase("core_lt", "scalar", "scalar", "4 .<. 5", "4 .<. 5"),
    ParityCase("core_geq", "scalar", "scalar", "5 .>=. 4", "5 .>=. 4"),
    ParityCase("core_gt", "scalar", "scalar", "5 .>. 4", "5 .>. 4"),
    ParityCase("max", "scalar", "gray", "max(a)", "max(a)"),
    ParityCase("min", "scalar", "gray", "min(a)", "min(a)"),
    ParityCase("avg", "scalar", "gray", "avg(a,ma)", "avg(a,ma)", atol=1e-4),
    ParityCase("volume", "scalar", "gray", "volume(ma)", "volume(ma)"),
)

IMAGE_CASES: tuple[ParityCase, ...] = (
    ParityCase("bconstant", "image", "gray", "bconstant(true)", "bconstant(true)"),
    ParityCase("tt", "image", "gray", "tt", "tt"),
    ParityCase("ff", "image", "gray", "ff", "ff"),
    ParityCase("not", "image", "gray", "not(ma)", "not(ma)"),
    ParityCase("and", "image", "gray", "and(ma,mb)", "and(ma,mb)"),
    ParityCase("or", "image", "gray", "or(ma,mb)", "or(ma,mb)"),
    ParityCase("dt", "image", "gray", "dt(ma)", "dt(ma)", atol=1e-4),
    ParityCase("constant", "image", "gray", "constant(3.5)", "constant(3.5)"),
    ParityCase("eq_sv", "image", "gray", "50 .= a", "50 .= a"),
    ParityCase("geq_sv", "image", "gray", "50 .<= a", "50 .<= a"),
    ParityCase("leq_sv", "image", "gray", "50 .>= a", "50 .>= a"),
    ParityCase("between", "image", "gray", "between(50,120,a)", "between(50,120,a)"),
    ParityCase("abs", "image", "gray", "abs(a - b)", "abs(a - b)"),
    ParityCase("add", "image", "gray", "a + b", "a + b"),
    ParityCase("mul", "image", "gray", "a * b", "a * b"),
    ParityCase("div", "image", "gray", "(a +. 1) / (b +. 1)", "(a +. 1) / (b +. 1)"),
    ParityCase("sub", "image", "gray", "a - b", "a - b"),
    ParityCase("mask", "image", "gray", "mask(a,ma)", "mask(a,ma)"),
    ParityCase("div_sv", "image", "gray", "100 ./ (a +. 1)", "100 ./ (a +. 1)"),
    ParityCase("sub_sv", "image", "gray", "100 .- a", "100 .- a"),
    ParityCase("div_vs", "image", "gray", "(a +. 1) /. 2", "(a +. 1) /. 2"),
    ParityCase("sub_vs", "image", "gray", "a -. 2", "a -. 2"),
    ParityCase("add_vs", "image", "gray", "a +. 2", "a +. 2"),
    ParityCase("mul_vs", "image", "gray", "a *. 2", "a *. 2"),
    ParityCase("near", "image", "gray", "near(ma)", "near(ma)"),
    ParityCase("interior", "image", "gray", "interior(ma)", "interior(ma)"),
    ParityCase("through", "image", "gray", "through(ma,mb)", "through(ma,mb)"),
    ParityCase(
        "crossCorrelation",
        "image",
        "gray",
        "crossCorrelation(1,a,b,tt,min(b),max(b),8)",
        "crossCorrelation(1,a,b,tt,min(b),max(b),8)",
        atol=1e-3,
    ),
    ParityCase("border", "image", "gray", "border", "border"),
    ParityCase("x", "image", "gray", "x", "x"),
    ParityCase("y", "image", "gray", "y", "y"),
    ParityCase("z", "image", "gray", "z", "z"),
    ParityCase("intensity", "image", "gray", "intensity(m1)", "intensity(m1)"),
    ParityCase("maxvol", "image", "gray", "maxvol(ma)", "maxvol(ma)"),
    ParityCase(
        "percentiles",
        "image",
        "gray",
        "percentiles(a,ma,0.5)",
        "percentiles(a,ma,0.5)",
        atol=1e-4,
    ),
    ParityCase("lcc", "image", "gray", "lcc(ma)", "lcc(ma)"),
    ParityCase("otsu", "image", "gray", "otsu(a,ma,16)", "otsu(a,ma,16)"),
    ParityCase("red", "image", "color", "red(c1)", "red(c1)"),
    ParityCase("green", "image", "color", "green(c1)", "green(c1)"),
    ParityCase("blue", "image", "color", "blue(c1)", "blue(c1)"),
    ParityCase("alpha", "image", "color", "alpha(c1)", "alpha(c1)"),
    ParityCase(
        "rgb",
        "image",
        "color",
        "rgb(red(c1),green(c1),blue(c1))",
        "rgb(red(c1),green(c1),blue(c1))",
    ),
    ParityCase(
        "rgba",
        "image",
        "color",
        "rgba(red(c1),green(c1),blue(c1),alpha(c1))",
        "rgba(red(c1),green(c1),blue(c1),alpha(c1))",
    ),
)

ALL_CASES: tuple[ParityCase, ...] = SCALAR_CASES + IMAGE_CASES


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
def parity_inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("vox1_legacy_parity")
    gray1_path = root / "gray1.nii.gz"
    gray2_path = root / "gray2.nii.gz"
    color_path = root / "color.png"

    base = np.arange(6 * 7 * 5, dtype=np.float32).reshape(6, 7, 5)
    second = (np.flip(base, axis=0) * 0.65) + 7.0

    gray1 = sitk.GetImageFromArray(base, isVector=False)
    gray2 = sitk.GetImageFromArray(second.astype(np.float32), isVector=False)
    gray1.SetSpacing((0.7, 1.3, 2.1))
    gray2.CopyInformation(gray1)

    color = np.zeros((9, 11, 3), dtype=np.uint8)
    color[..., 0] = np.linspace(0, 255, 11, dtype=np.uint8)
    color[..., 1] = np.linspace(255, 0, 9, dtype=np.uint8)[:, None]
    color[..., 2] = ((color[..., 0].astype(np.uint16) + color[..., 1].astype(np.uint16)) // 2).astype(np.uint8)
    color_img = sitk.GetImageFromArray(color, isVector=True)

    sitk.WriteImage(gray1, str(gray1_path))
    sitk.WriteImage(gray2, str(gray2_path))
    sitk.WriteImage(color_img, str(color_path))

    return {"gray1": gray1_path, "gray2": gray2_path, "color": color_path}


def _build_prelude(case_domain: str, backend: str, inputs: dict[str, Path]) -> str:
    if case_domain == "scalar":
        if backend == "legacy":
            return 'import "stdlib.imgql"\n'
        return ""

    if case_domain == "gray":
        if backend == "legacy":
            return (
                'import "stdlib.imgql"\n'
                f'load m1 = "{inputs["gray1"]}"\n'
                f'load m2 = "{inputs["gray2"]}"\n'
                "let a = intensity(m1)\n"
                "let b = intensity(m2)\n"
                "let ma = 50 .<= a\n"
                "let mb = 70 .<= b\n"
            )
        return (
            'import "simpleitk"\n'
            f'let m1 = ReadImage("{inputs["gray1"]}")\n'
            f'let m2 = ReadImage("{inputs["gray2"]}")\n'
            "let a = intensity(m1)\n"
            "let b = intensity(m2)\n"
            "let ma = 50 .<= a\n"
            "let mb = 70 .<= b\n"
        )

    if case_domain == "color":
        if backend == "legacy":
            return (
                'import "stdlib.imgql"\n'
                f'load c1 = "{inputs["color"]}"\n'
            )
        return (
            'import "simpleitk"\n'
            f'let c1 = ReadImage("{inputs["color"]}")\n'
        )

    raise ValueError(f"Unsupported domain: {case_domain}")


def _run_legacy(
    legacy_binary: Path,
    workdir: Path,
    program_text: str,
) -> subprocess.CompletedProcess[str]:
    program_path = workdir / "legacy_case.imgql"
    program_path.write_text(program_text, encoding="utf-8")
    return subprocess.run(
        [str(legacy_binary), str(program_path)],
        cwd=str(workdir),
        check=False,
        capture_output=True,
        text=True,
    )


def _parse_legacy_scalar(stdout: str) -> bool | float:
    match = re.search(r"\[user\]\s+res=([^\n]+)", stdout)
    if match is None:
        raise AssertionError(f"Missing legacy scalar output in:\n{stdout}")
    raw = match.group(1).strip()
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return float(raw)


def _run_v2(program_text: str):
    program = parse_program_content(program_text)
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success, result.failed_operations
    target_goal = next((goal for goal in prepared.plan.goals if goal.name == "res"), None)
    if target_goal is None:
        raise AssertionError("Missing 'res' goal in v2 test program")
    return prepared.materialization_store.get(target_goal.id)


def _assert_scalar_parity(expected, actual, atol: float) -> None:
    if isinstance(expected, bool):
        assert bool(actual) is expected
        return
    assert float(actual) == pytest.approx(float(expected), abs=atol, rel=1e-6)


def _assert_image_parity(expected: sitk.Image, actual: sitk.Image, atol: float) -> None:
    expected_arr = sitk.GetArrayFromImage(expected)
    actual_arr = sitk.GetArrayFromImage(actual)
    assert expected_arr.shape == actual_arr.shape

    if np.issubdtype(expected_arr.dtype, np.floating) or np.issubdtype(actual_arr.dtype, np.floating):
        assert np.allclose(expected_arr, actual_arr, atol=atol, rtol=1e-5, equal_nan=True)
    else:
        assert np.array_equal(expected_arr, actual_arr)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.parametrize("case", ALL_CASES, ids=[case.name for case in ALL_CASES])
def test_vox1_matches_legacy_experimental_operator_by_operator(
    case: ParityCase,
    legacy_binary: Path,
    parity_inputs: dict[str, Path],
    tmp_path: Path,
):
    legacy_prelude = _build_prelude(case.domain, "legacy", parity_inputs)
    v2_prelude = _build_prelude(case.domain, "v2", parity_inputs)

    if case.kind == "scalar":
        legacy_program = f"{legacy_prelude}print \"res\" {case.legacy_expr}\n"
        legacy_run = _run_legacy(legacy_binary, tmp_path, legacy_program)
        assert legacy_run.returncode == 0, (
            f"Legacy run failed for {case.name}\n"
            f"STDOUT:\n{legacy_run.stdout}\nSTDERR:\n{legacy_run.stderr}"
        )
        legacy_value = _parse_legacy_scalar(legacy_run.stdout)

        seed = ""
        if case.domain == "gray":
            seed = 'print "seed" a\n'
        elif case.domain == "color":
            seed = 'print "seed" intensity(c1)\n'
        v2_program = f"{v2_prelude}{seed}print \"res\" {case.v2_expr}\n"
        v2_value = _run_v2(v2_program)
        _assert_scalar_parity(legacy_value, v2_value, case.atol)
        return

    legacy_output = tmp_path / f"legacy_{case.name}.nii.gz"
    legacy_program = f'{legacy_prelude}save "{legacy_output}" {case.legacy_expr}\n'
    legacy_run = _run_legacy(legacy_binary, tmp_path, legacy_program)
    assert legacy_run.returncode == 0, (
        f"Legacy run failed for {case.name}\n"
        f"STDOUT:\n{legacy_run.stdout}\nSTDERR:\n{legacy_run.stderr}"
    )
    assert legacy_output.exists(), f"Legacy output missing for {case.name}: {legacy_output}"
    legacy_image = sitk.ReadImage(str(legacy_output))

    seed = ""
    if case.domain == "gray":
        seed = 'print "seed" a\n'
    elif case.domain == "color":
        seed = 'print "seed" intensity(c1)\n'
    v2_program = f'{v2_prelude}{seed}print "res" {case.v2_expr}\n'
    v2_value = _run_v2(v2_program)
    assert isinstance(v2_value, sitk.Image), f"Expected image output for {case.name}"
    _assert_image_parity(legacy_image, v2_value, case.atol)
