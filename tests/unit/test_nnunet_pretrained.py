"""Starting weights are an input to the experiment, not a setting of the run.

`nnUNetv2_train -pretrained_weights` is how a model is started from another
model's checkpoint instead of random init -- the "pretrain on the oracle, then
fine-tune" shape. It was unreachable from a program, so what is pinned here is
that it travels as an argument: it reaches the command line, it is made
absolute, a missing file is refused before any compute is spent, and it is
recorded on the model, because two models trained on the same data from
different starting weights are two different models.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.primitives.nnunet import runtime
from voxlogica.primitives.nnunet.cases import build_model


def a_model(**extra):
    model = dict(
        work_root="/w", dataset_id=900, dataset_folder="Dataset900_Demo",
        configuration="3d_fullres", modalities=["FLAIR"], trained_folds=[0],
        trainer_dir="/w/t",
    )
    model.update(extra)
    return build_model(**model)


def test_a_model_records_where_its_weights_started():
    assert a_model()["pretrained"] == ""
    assert a_model(pretrained="/w/ckpt.pth")["pretrained"] == "/w/ckpt.pth"


def test_a_missing_checkpoint_is_refused_before_anything_is_computed(tmp_path, monkeypatch):
    """nnU-Net's own failure for this arrives after preprocessing is paid for."""
    def must_not_run(*args, **kwargs):
        raise AssertionError("nothing may run before the checkpoint is checked")

    monkeypatch.setattr(runtime, "require_nnunet", lambda: None)
    monkeypatch.setattr(runtime, "run_cli", must_not_run)

    with pytest.raises(ValueError) as excinfo:
        runtime.train_model(
            layout={"work_dir": str(tmp_path), "nnunet_results": str(tmp_path),
                    "dataset_folder": "Dataset900_Demo",
                    "dataset_dir": str(tmp_path / "Dataset900_Demo")},
            dataset_id=900, dataset_name="Demo", configuration="3d_fullres",
            modalities=["FLAIR"], nfolds=1, device="cpu", labels={"background": 0},
            pretrained=str(tmp_path / "absent.pth"),
        )

    assert "absent.pth" in str(excinfo.value)


def test_the_checkpoint_reaches_the_command_line_absolute(tmp_path, monkeypatch):
    """The command runs with cwd=work_root, so a relative path would move."""
    ckpt = tmp_path / "ckpt.pth"
    ckpt.write_bytes(b"weights")
    monkeypatch.chdir(tmp_path)

    seen: list[list[str]] = []
    monkeypatch.setattr(runtime, "require_nnunet", lambda: None)
    monkeypatch.setattr(runtime, "run_cli", lambda command, **kw: seen.append(command))
    monkeypatch.setattr(runtime, "trainer_dir", lambda *a, **k: tmp_path / "t")
    monkeypatch.setattr(runtime, "determine_postprocessing_for", lambda *a, **k: None)

    model = runtime.train_model(
        layout={"work_dir": str(tmp_path), "nnunet_results": str(tmp_path),
                "dataset_folder": "Dataset900_Demo",
                "dataset_dir": str(tmp_path / "Dataset900_Demo")},
        dataset_id=900, dataset_name="Demo", configuration="3d_fullres",
        modalities=["FLAIR"], nfolds=1, device="cpu", labels={"background": 0},
        pretrained="ckpt.pth",
    )

    train = [c for c in seen if "-pretrained_weights" in c]
    assert train, [c[:3] for c in seen]
    given = train[0][train[0].index("-pretrained_weights") + 1]
    assert Path(given).is_absolute() and Path(given) == ckpt.resolve()
    assert model["pretrained"] == str(ckpt.resolve())


def test_training_from_scratch_names_no_checkpoint(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(runtime, "require_nnunet", lambda: None)
    monkeypatch.setattr(runtime, "run_cli", lambda command, **kw: seen.append(command))
    monkeypatch.setattr(runtime, "trainer_dir", lambda *a, **k: tmp_path / "t")
    monkeypatch.setattr(runtime, "determine_postprocessing_for", lambda *a, **k: None)

    runtime.train_model(
        layout={"work_dir": str(tmp_path), "nnunet_results": str(tmp_path),
                "dataset_folder": "Dataset900_Demo",
                "dataset_dir": str(tmp_path / "Dataset900_Demo")},
        dataset_id=900, dataset_name="Demo", configuration="3d_fullres",
        modalities=["FLAIR"], nfolds=1, device="cpu", labels={"background": 0},
    )

    assert not any("-pretrained_weights" in c for c in seen)
