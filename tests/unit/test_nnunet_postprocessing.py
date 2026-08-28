"""nnU-Net's own postprocessing step is part of the documented workflow.

The documented order is train -> nnUNetv2_find_best_configuration ->
nnUNetv2_apply_postprocessing -> predict. This module used to stop after
training and predict raw, which is not a neutral simplification: the middle
step exists to remove the spurious-component failures that drag a mean Dice
down while the median stays where it should be.

What is pinned here is the part that has to hold whether or not nnU-Net is
installed: the decision is asked once, a "raw output is best" answer is a real
answer and is not asked again, a model trained before this existed can still
gain the answer without being retrained, and a missing answer changes nothing.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from voxlogica.primitives.nnunet import runtime


@pytest.fixture(autouse=True)
def clear_resolved_cache():
    runtime._PP_RESOLVED.clear()
    yield
    runtime._PP_RESOLVED.clear()


def model_at(tmp_path, **extra):
    model = {
        "work_root": str(tmp_path),
        "dataset_folder": "Dataset900_Demo",
        "trainer_dir": str(tmp_path / "nnUNet_results" / "Dataset900_Demo" / "t"),
        "trained_folds": [0],
    }
    model.update(extra)
    return model


# --- what the decision is applied to ---------------------------------------


def test_no_decision_leaves_the_segmentation_alone():
    seg = np.array([[0, 1], [1, 0]], dtype=np.uint8)

    assert runtime.apply_postprocessing_to_array(seg, None) is seg
    assert runtime.apply_postprocessing_to_array(seg, {"operations": []}) is seg


def test_the_decided_operation_is_the_one_that_runs(monkeypatch):
    seen: list[dict] = []

    def fake_remove(segmentation, **kwargs):
        seen.append(kwargs)
        return segmentation * 2

    module = types.ModuleType("nnunetv2.postprocessing.remove_connected_components")
    module.remove_all_but_largest_component_from_segmentation = fake_remove
    monkeypatch.setitem(sys.modules, "nnunetv2", types.ModuleType("nnunetv2"))
    monkeypatch.setitem(sys.modules, "nnunetv2.postprocessing",
                        types.ModuleType("nnunetv2.postprocessing"))
    monkeypatch.setitem(sys.modules,
                        "nnunetv2.postprocessing.remove_connected_components", module)

    out = runtime.apply_postprocessing_to_array(
        np.ones((2, 2), dtype=np.uint8),
        {"operations": ["remove_all_but_largest_component_from_segmentation"],
         "kwargs": [{"labels_or_regions": [1]}]},
    )

    assert seen == [{"labels_or_regions": [1]}]
    assert out.tolist() == [[2, 2], [2, 2]]


def test_an_operation_this_version_does_not_know_is_reported_not_silently_dropped(caplog):
    seg = np.ones((2, 2), dtype=np.uint8)

    module = types.ModuleType("nnunetv2.postprocessing.remove_connected_components")
    module.remove_all_but_largest_component_from_segmentation = lambda s, **k: s
    sys.modules.setdefault("nnunetv2", types.ModuleType("nnunetv2"))
    sys.modules.setdefault("nnunetv2.postprocessing", types.ModuleType("nnunetv2.postprocessing"))
    saved = sys.modules.get("nnunetv2.postprocessing.remove_connected_components")
    sys.modules["nnunetv2.postprocessing.remove_connected_components"] = module
    try:
        with caplog.at_level("WARNING"):
            out = runtime.apply_postprocessing_to_array(
                seg, {"operations": ["some_future_step"], "kwargs": [{}]})
    finally:
        if saved is None:
            sys.modules.pop("nnunetv2.postprocessing.remove_connected_components", None)
        else:
            sys.modules["nnunetv2.postprocessing.remove_connected_components"] = saved

    assert out is seg
    assert "some_future_step" in caplog.text


# --- how often the decision is asked for -----------------------------------


def test_a_decision_already_on_the_model_is_not_asked_for_again(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the model already carries a decision")

    monkeypatch.setattr(runtime, "determine_postprocessing_for", refuse)

    decision = {"operations": ["remove_all_but_largest_component_from_segmentation"],
                "kwargs": [{"labels_or_regions": [1]}]}
    assert runtime.resolve_postprocessing(model_at(tmp_path, postprocessing=decision)) == decision


def test_raw_output_is_best_is_an_answer_and_is_not_asked_again(tmp_path, monkeypatch):
    """A key present and None means the question was put and could not be answered.

    Re-asking on every case would pay for the whole validation-set metric
    computation once per predicted image.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("the question was already put")

    monkeypatch.setattr(runtime, "determine_postprocessing_for", refuse)

    assert runtime.resolve_postprocessing(model_at(tmp_path, postprocessing=None)) is None


def test_a_model_trained_before_this_existed_gains_the_answer_without_retraining(
        tmp_path, monkeypatch):
    calls: list[tuple] = []

    def record(trainer_path, labels_dir, folds, work_root):
        calls.append((trainer_path, labels_dir, folds, work_root))
        return {"operations": [], "kwargs": []}

    monkeypatch.setattr(runtime, "determine_postprocessing_for", record)

    model = model_at(tmp_path)          # no "postprocessing" key at all
    first = runtime.resolve_postprocessing(model)
    second = runtime.resolve_postprocessing(model)

    assert first == {"operations": [], "kwargs": []}
    assert second == first
    assert len(calls) == 1, "the answer is determined once per trained model, not per case"
    assert calls[0][1] == tmp_path / "nnUNet_raw" / "Dataset900_Demo" / "labelsTr"
    assert calls[0][2] == [0]


def test_a_model_handle_without_the_fields_this_needs_still_predicts(caplog):
    """A prediction that could not be postprocessed still has to be a prediction."""
    with caplog.at_level("WARNING"):
        assert runtime.resolve_postprocessing({"trainer_dir": "/nowhere"}) is None


def test_the_labels_a_model_was_trained_against_are_derivable_from_the_model(tmp_path):
    labels = runtime.model_labels_dir(model_at(tmp_path))

    assert labels == tmp_path / "nnUNet_raw" / "Dataset900_Demo" / "labelsTr"


# --- when the question cannot be put ---------------------------------------


def test_nothing_to_determine_from_is_not_an_error(tmp_path):
    """Training must not fail because postprocessing could not be decided."""
    trainer = tmp_path / "trainer"
    trainer.mkdir()

    assert runtime.determine_postprocessing_for(
        trainer, tmp_path / "absent", [0], tmp_path) is None


def test_the_cached_decision_lives_where_nnunet_leaves_it(tmp_path):
    assert (runtime.postprocessing_pkl(tmp_path, 3)
            == tmp_path / "fold_3" / "validation" / "postprocessing.pkl")


def keep_largest(segmentation, **kwargs):
    """Stands in for an nnU-Net postprocessing function, so a pickle can name one."""
    return segmentation


def test_a_decision_already_on_disk_is_read_rather_than_determined_again(tmp_path, monkeypatch):
    """nnU-Net caches its own answer next to the validation predictions.

    Re-determining costs the whole validation-set metric computation, twice
    over, for an answer that is already written down.
    """
    import pickle

    trainer = tmp_path / "trainer"
    (trainer / "fold_0" / "validation").mkdir(parents=True)
    (trainer / "plans.json").write_text("{}")
    (trainer / "dataset.json").write_text("{}")
    labels = tmp_path / "labelsTr"
    labels.mkdir()
    runtime.postprocessing_pkl(trainer, 0).write_bytes(
        pickle.dumps(([keep_largest], [{"labels_or_regions": [1]}])))

    def refuse(*args, **kwargs):
        raise AssertionError("the decision was already on disk")

    monkeypatch.setattr(runtime, "run_cli", refuse)

    decision = runtime.determine_postprocessing_for(trainer, labels, [0], tmp_path)

    assert decision == {"operations": ["keep_largest"],
                        "kwargs": [{"labels_or_regions": [1]}]}
