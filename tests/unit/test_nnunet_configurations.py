"""Several configurations, and nnU-Net choosing between them.

The documented workflow plans 2d, 3d_fullres and 3d_lowres, trains each, and
runs nnUNetv2_find_best_configuration to say which single model -- or which
ensemble of two -- actually scored best on the cross-validation. This namespace
could only ever train one configuration, so the choice fell to whoever wrote the
program, on no evidence.

Naming one configuration must stay exactly what it was: the one-configuration
case is not a special case, it is the same code with nothing to choose between.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxlogica.primitives.nnunet import runtime
from voxlogica.primitives.nnunet.cases import normalize_configurations


LAYOUT_KEYS = ("work_dir", "nnunet_results", "dataset_folder", "dataset_dir")


def layout_at(tmp_path):
    return {"work_dir": str(tmp_path), "nnunet_results": str(tmp_path / "res"),
            "dataset_folder": "Dataset900_Demo",
            "dataset_dir": str(tmp_path / "raw" / "Dataset900_Demo")}


@pytest.fixture
def trained(monkeypatch, tmp_path):
    """Run train_model with every child process replaced by a recording."""
    seen: list[list[str]] = []
    monkeypatch.setattr(runtime, "require_nnunet", lambda: None)
    monkeypatch.setattr(runtime, "run_cli", lambda command, **kw: seen.append(command))
    monkeypatch.setattr(runtime, "trainer_dir",
                        lambda root, folder, config, tr=None, pl=None: tmp_path / f"t_{config}")
    monkeypatch.setattr(runtime, "determine_postprocessing_for", lambda *a, **k: None)
    return seen


def run_train(tmp_path, configurations, **extra):
    return runtime.train_model(
        layout=layout_at(tmp_path), dataset_id=900, dataset_name="Demo",
        configurations=configurations, modalities=["FLAIR"], nfolds=1,
        device="cpu", labels={"background": 0}, **extra)


# --- naming one configuration ---------------------------------------------


def test_one_configuration_trains_it_and_asks_nobody(trained, tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("nothing to choose between")

    monkeypatch.setattr(runtime, "find_best_configuration_for", refuse)

    model = run_train(tmp_path, ["3d_fullres"])

    assert model["configuration"] == "3d_fullres"
    assert model["configurations"] == ["3d_fullres"]
    assert [m["configuration"] for m in model["selection"]] == ["3d_fullres"]


def test_softmax_is_saved_only_when_there_is_something_to_ensemble_with(trained, tmp_path):
    """--npz writes one probability array per validation case. It is not free."""
    run_train(tmp_path, ["3d_fullres"])

    assert not any("--npz" in c for c in trained)


# --- naming several --------------------------------------------------------


def test_every_configuration_is_preprocessed_and_trained(trained, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "find_best_configuration_for", lambda **k: ([], ""))

    run_train(tmp_path, ["2d", "3d_fullres", "3d_lowres"])

    plan = next(c for c in trained if "nnUNetv2_plan_and_preprocess" in c[0])
    assert plan[plan.index("-c") + 1:plan.index("-c") + 4] == ["2d", "3d_fullres", "3d_lowres"]

    trains = [c for c in trained if "nnUNetv2_train" in c[0]]
    assert [c[2] for c in trains] == ["2d", "3d_fullres", "3d_lowres"]
    assert all("--npz" in c for c in trains)


def test_what_nnunet_chose_is_what_predicts(trained, tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime, "find_best_configuration_for",
        lambda **k: ([{"configuration": "2d", "plans": "nnUNetPlans", "trainer": "nnUNetTrainer"},
                      {"configuration": "3d_fullres", "plans": "nnUNetPlans",
                       "trainer": "nnUNetTrainer"}], ""))

    model = run_train(tmp_path, ["2d", "3d_fullres", "3d_lowres"])

    assert [m["configuration"] for m in model["selection"]] == ["2d", "3d_fullres"]
    assert all(m["trainer_dir"] for m in model["selection"])


def test_a_choice_that_cannot_be_made_falls_back_to_what_was_asked_first(
        trained, tmp_path, monkeypatch):
    """A failed selection must not fail the training that has already been paid for."""
    monkeypatch.setattr(runtime, "find_best_configuration_for", lambda **k: ([], ""))

    model = run_train(tmp_path, ["3d_fullres", "2d"])

    assert [m["configuration"] for m in model["selection"]] == ["3d_fullres"]


# --- reading nnU-Net's answer ---------------------------------------------


def test_the_selection_is_read_from_nnunets_own_file(tmp_path, monkeypatch):
    results = tmp_path / "res" / "Dataset900_Demo"
    results.mkdir(parents=True)
    (results / "inference_information.json").write_text(json.dumps({
        "best_model_or_ensemble": {
            "selected_model_or_models": [
                {"configuration": "2d", "plans": "nnUNetPlans", "trainer": "nnUNetTrainer"},
                {"configuration": "3d_fullres", "plans": "nnUNetPlans",
                 "trainer": "nnUNetTrainer"}],
            "postprocessing_file": "/res/pp.pkl"}}))
    monkeypatch.setattr(runtime, "run_cli", lambda *a, **k: None)

    selection, pp = runtime.find_best_configuration_for(
        dataset_id=900, results_root=tmp_path / "res", folder="Dataset900_Demo",
        configurations=["2d", "3d_fullres"], plans_id="nnUNetPlans",
        trainer_class="nnUNetTrainer", folds=[0], work_root=tmp_path, env={})

    assert [m["configuration"] for m in selection] == ["2d", "3d_fullres"]
    assert pp == "/res/pp.pkl"


def test_a_command_that_fails_is_not_an_error_here(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise ValueError("nnUNetv2_find_best_configuration exited 1")

    monkeypatch.setattr(runtime, "run_cli", boom)

    assert runtime.find_best_configuration_for(
        dataset_id=900, results_root=tmp_path, folder="Dataset900_Demo",
        configurations=["2d"], plans_id="nnUNetPlans", trainer_class="nnUNetTrainer",
        folds=[0], work_root=tmp_path, env={}) == ([], "")


# --- the argument itself ---------------------------------------------------


@pytest.mark.parametrize("given,expected", [
    ("3d_fullres", ["3d_fullres"]),
    (["2d", "3d_fullres"], ["2d", "3d_fullres"]),
    (["2d", "2d", " 3d_fullres "], ["2d", "3d_fullres"]),
])
def test_one_or_several_both_arrive_as_a_list(given, expected):
    assert normalize_configurations(given) == expected


@pytest.mark.parametrize("bad", [None, "", [], ["  "]])
def test_no_configuration_at_all_is_refused(bad):
    with pytest.raises(ValueError):
        normalize_configurations(bad)
