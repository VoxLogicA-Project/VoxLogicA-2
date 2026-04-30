from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.primitives.nnunet import kernels


@pytest.mark.unit
def test_train_rejects_non_bag_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="compute\\(\\)"):
        kernels.train(
            **{
                "0": "images",
                "1": "labels",
                "2": ["T1"],
                "3": str(tmp_path / "work"),
            }
        )


@pytest.mark.unit
def test_train_uses_shared_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeBag:
        def compute(self):
            return []

    def fake_materialize_bag_dataset(**kwargs):
        captured["materialize"] = kwargs
        return {
            "layout": {
                "work_dir": tmp_path / "work",
                "nnunet_results": tmp_path / "work" / "nnUNet_results",
                "padded_name": "Dataset007_Brain",
                "unpadded_name": "Dataset7_Brain",
            },
            "labels_sanitized": False,
            "label_value_map": {"case_001.nii.gz": [0, 1]},
        }

    def fake_run_training_pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return {
            "status": "success",
            "fold_results": [{"fold": 0, "status": "success"}],
            "training_time": 1.25,
        }

    monkeypatch.setattr(kernels, "_materialize_bag_dataset", fake_materialize_bag_dataset)
    monkeypatch.setattr(kernels, "_run_training_pipeline", fake_run_training_pipeline)

    result = kernels.train(
        **{
            "0": FakeBag(),
            "1": FakeBag(),
            "2": ["T1", "T2"],
            "3": str(tmp_path / "work"),
            "4": 7,
            "5": "Brain",
            "6": "3d_fullres",
            "7": 1,
        }
    )

    assert result["status"] == "success"
    assert result["trained_folds"] == [0]
    assert captured["materialize"]["dataset_id"] == 7
    assert captured["materialize"]["dataset_name"] == "Brain"
    assert captured["pipeline"]["configuration"] == "3d_fullres"
    assert captured["pipeline"]["device"] == "gpu"


@pytest.mark.unit
def test_train_directory_uses_shared_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    images_dir = tmp_path / "imagesTr"
    labels_dir = tmp_path / "labelsTr"
    work_dir = tmp_path / "work"
    images_dir.mkdir()
    labels_dir.mkdir()

    def fake_materialize_directory_dataset(**kwargs):
        captured["materialize"] = kwargs
        return {
            "layout": {
                "work_dir": work_dir,
                "nnunet_results": work_dir / "nnUNet_results",
                "padded_name": "Dataset003_Synthetic",
                "unpadded_name": "Dataset3_Synthetic",
            },
            "labels_sanitized": True,
            "label_value_map": {"case_001.nii.gz": [0, 2]},
        }

    def fake_run_training_pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return {"status": "success", "fold_results": [], "training_time": 0.0}

    monkeypatch.setattr(
        kernels,
        "_materialize_directory_dataset",
        fake_materialize_directory_dataset,
    )
    monkeypatch.setattr(kernels, "_run_training_pipeline", fake_run_training_pipeline)

    result = kernels.train_directory(
        **{
            "0": str(images_dir),
            "1": str(labels_dir),
            "2": ["FLAIR"],
            "3": str(work_dir),
            "4": 3,
            "5": "Synthetic",
            "6": "2d",
            "7": 2,
            "8": "cpu",
        }
    )

    assert result["status"] == "success"
    assert captured["materialize"]["modalities"] == ["FLAIR"]
    assert captured["pipeline"]["dataset_id"] == 3
    assert captured["pipeline"]["nfolds"] == 2
    assert captured["pipeline"]["device"] == "cpu"
