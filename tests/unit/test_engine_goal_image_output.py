"""Goal outputs (print/save/return) must expose native sitk.Image, not PolyArray.

Phase 0 makes ``PolyArray`` the engine's live-tier value for images. That
wrapper must never leak to a user-facing goal result: the strategy's
``_materialize`` seam unwraps it back to ``sitk.Image`` so the engine's
observable output is unchanged from the pre-fusion engine.
"""

from __future__ import annotations

import pytest
import SimpleITK as sitk

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

# A tiny all-in-numpy-free program: build a constant image, threshold it, print.
# `import "simpleitk"` gives BinaryThreshold etc.; the goal value is an image.
PROGRAM = """
import "simpleitk"
let img = ReadImage("{path}")
let mask = BinaryThreshold(img, 1.0, 255.0, 1, 0)
print "mask" mask
"""


@pytest.mark.unit
def test_print_goal_of_image_returns_sitk_not_polyarray(tmp_path, capsys) -> None:
    # A real on-disk image so ReadImage has something to load.
    arr_path = tmp_path / "in.nii.gz"
    src = sitk.GetImageFromArray(
        sitk.GetArrayFromImage(sitk.Image(4, 4, 4, sitk.sitkUInt8)) + 5
    )
    sitk.WriteImage(src, str(arr_path))

    program = PROGRAM.format(path=str(arr_path).replace("\\", "/"))
    result = ExecutionEngine(use_engine=True).execute_workplan(
        reduce_program(parse_program_content(program))
    )
    assert result.success is True

    out = capsys.readouterr().out
    # The wrapper's default repr would look like "<...PolyArray object at 0x...>".
    assert "PolyArray" not in out, f"PolyArray wrapper leaked to print output: {out!r}"
    assert "mask=" in out
