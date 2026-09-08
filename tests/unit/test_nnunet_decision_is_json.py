"""What comes off a model handle must survive json.dumps.

The handle is serialized into the run state and content-addressed by the
engine, so every value on it has to be plain Python. nnU-Net's postprocessing
decision is read back from a pickle it wrote, and its kwargs carry label ids as
numpy integers -- which is not JSON.

That combination ended a COMPLETED 1000-epoch training with

    ERROR: nnUNet training failed: Object of type int64 is not JSON serializable

after the weights were already written and the validation Dice already
computed. Thirteen hours of GPU discarded on the last line of bookkeeping, and
the failure is invisible until the very end of the longest step there is --
which is exactly the kind of thing a unit test should be holding.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from voxlogica.primitives.nnunet.runtime import _plain


@pytest.mark.unit
def test_numpy_scalars_become_python_numbers() -> None:
    out = _plain({"labels": np.int64(1), "threshold": np.float32(0.5)})
    assert out == {"labels": 1, "threshold": pytest.approx(0.5)}
    assert type(out["labels"]) is int
    assert not isinstance(out["threshold"], np.generic)


@pytest.mark.unit
def test_the_shape_nnunet_actually_hands_back_is_serializable() -> None:
    """`remove_all_but_largest_component_from_segmentation` and its kwargs.

    Nested exactly as `_decision_from_pickle` builds it: a list of kwarg dicts,
    label ids inside collections rather than at the top level.
    """
    decision = {
        "operations": ["remove_all_but_largest_component_from_segmentation"],
        "kwargs": [_plain({
            "labels_or_regions": [np.int64(1)],
            "background_label": np.int64(0),
            "volume_per_voxel": np.float64(1.0),
        })],
    }
    # The assertion IS the round trip: this raised TypeError before.
    assert json.loads(json.dumps(decision)) == decision


@pytest.mark.unit
def test_arrays_and_nesting_survive() -> None:
    out = _plain({"a": [np.int64(3), {"b": np.array([1, 2])}], "c": (np.float32(1.5),)})
    assert out == {"a": [3, {"b": [1, 2]}], "c": [1.5]}
    json.dumps(out)  # must not raise


@pytest.mark.unit
def test_values_that_are_already_plain_are_untouched() -> None:
    """A conversion that rewrites what it should leave alone is its own bug."""
    original = {"trainer": "nnUNetTrainer", "folds": [0], "ok": True, "none": None}
    assert _plain(original) == original
