from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.execution_strategy.strict import StrictExecutionStrategy
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


def _run_program(program_text: str) -> dict[str, object]:
    program = parse_program_content(program_text)
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success, result.failed_operations
    return {
        goal.name: prepared.materialization_store.get(goal.id)
        for goal in prepared.plan.goals
    }


def _write_input(path: Path) -> None:
    values = np.arange(27, dtype=np.float32).reshape(3, 3, 3)
    image = sitk.GetImageFromArray(values, isVector=False)
    image.SetSpacing((1.0, 1.0, 1.0))
    sitk.WriteImage(image, str(path))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lhs", "rhs"),
    [
        ("a +. 2", "a + 2"),
        ("2 .+ a", "2 + a"),
        ("a -. 2", "a - 2"),
        ("2 .- a", "2 - a"),
        ("a *. 2", "a * 2"),
        ("2 .* a", "2 * a"),
        ("(a +. 1) /. 2", "(a +. 1) / 2"),
        ("2 ./ (a +. 1)", "2 / (a +. 1)"),
    ],
)
def test_vox1_overloaded_arithmetic_matches_dotted_forms(tmp_path: Path, lhs: str, rhs: str):
    img_path = tmp_path / "sample.nii.gz"
    _write_input(img_path)

    program = (
        'import "simpleitk"\n'
        f'let m1 = ReadImage("{img_path}")\n'
        "let a = intensity(m1)\n"
        f'print "lhs" {lhs}\n'
        f'print "rhs" {rhs}\n'
    )
    outputs = _run_program(program)
    lhs_img = outputs["lhs"]
    rhs_img = outputs["rhs"]
    assert isinstance(lhs_img, sitk.Image)
    assert isinstance(rhs_img, sitk.Image)
    lhs_arr = sitk.GetArrayFromImage(lhs_img)
    rhs_arr = sitk.GetArrayFromImage(rhs_img)
    assert np.allclose(lhs_arr, rhs_arr, atol=1e-6, rtol=1e-6)
