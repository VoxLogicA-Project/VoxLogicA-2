"""A finished training must not be thrown away by its own bookkeeping.

WHAT THIS COSTS WHEN IT IS WRONG. On 2026-09-18 a 1000-epoch nnU-Net fold
finished -- mean validation Dice 0.9353, `checkpoint_final.pth` written -- and
the run then died with:

    ERROR: nnUNet training failed: Object of type int64 is not JSON serializable

nnU-Net's postprocessing decision is a pickle, and for this dataset it carried
`remove_all_but_largest_component_from_segmentation` with a numpy `int64`
label. That dict goes into the work root's state file and onto the model
handle, both of which are JSON. Twenty-one hours of GPU time were reported as a
*training* failure, which is precisely what it was not: the training had
succeeded and was on disk.

Two guards, tested here: the decision is normalised where the pickle is read,
and the state writer can encode a foreign scalar whatever else arrives later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxlogica.primitives.nnunet.materialize import load_state, save_state
from voxlogica.primitives.nnunet.runtime import _json_safe, _native


@pytest.mark.unit
def test_a_numpy_scalar_becomes_a_python_value() -> None:
    numpy = pytest.importorskip("numpy")
    assert _native(numpy.int64(1)) == 1
    assert isinstance(_native(numpy.int64(1)), int)
    assert _native(numpy.float32(0.5)) == pytest.approx(0.5)
    assert _native(numpy.array([1, 2])) == [1, 2]


@pytest.mark.unit
def test_the_real_decision_shape_survives_json() -> None:
    """The exact structure that killed the run: operations plus kwargs from the
    pickle, with a numpy label inside."""
    numpy = pytest.importorskip("numpy")
    decision = _json_safe({
        "operations": ["remove_all_but_largest_component_from_segmentation"],
        "kwargs": [{"labels_or_regions": numpy.int64(1)}],
    })
    encoded = json.dumps(decision)          # must not raise
    assert json.loads(encoded)["kwargs"][0]["labels_or_regions"] == 1


@pytest.mark.unit
def test_the_state_file_round_trips_a_foreign_scalar(tmp_path: Path) -> None:
    """The belt: even an un-normalised field must not cost a run."""
    numpy = pytest.importorskip("numpy")
    save_state(tmp_path, {"dataset_id": 900,
                          "postprocessing": {"kwargs": [{"label": numpy.int64(7)}]}})
    state = load_state(tmp_path)
    assert state is not None
    assert state["postprocessing"]["kwargs"][0]["label"] == 7
