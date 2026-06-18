from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.primitives.nnunet import kernels as nnunet_kernels
from voxlogica.reducer import reduce_program_with_bindings
from voxlogica.storage import NoCacheStorageBackend


PROGRAM_PATH = Path(__file__).with_name("train_predict_circles.imgql")


def _segmentation_array(value) -> np.ndarray:
    import SimpleITK as sitk

    return np.asarray(sitk.GetArrayFromImage(value))


@pytest.mark.e2e
@pytest.mark.slow
def test_nnunet_segments_circles_not_squares(tmp_path: Path) -> None:
    env = nnunet_kernels.env_check()
    if not env.get("ready"):
        pytest.skip(f"nnUNet runtime not ready: {env.get('issues')}")

    work_root = tmp_path / "work"
    program_text = PROGRAM_PATH.read_text(encoding="utf-8").replace(
        "__WORK_ROOT__",
        str(work_root),
    )
    syntax = parse_program_content(program_text, source_name=str(PROGRAM_PATH))
    workplan, bindings = reduce_program_with_bindings(syntax)

    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True)
    prepared = engine.compile_plan(workplan)
    result = engine.run_prepared(prepared)
    assert result.success, result.failed_operations

    circle_seg = _segmentation_array(prepared.values[bindings["circle_seg"].operation_id])
    square_seg = _segmentation_array(prepared.values[bindings["square_seg"].operation_id])

    assert float(circle_seg[32, 40]) >= 0.5
    assert (square_seg >= 0.5).sum() <= (circle_seg >= 0.5).sum()
