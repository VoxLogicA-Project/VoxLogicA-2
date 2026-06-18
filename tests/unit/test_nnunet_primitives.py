from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxlogica.primitives.nnunet import kernels


@pytest.mark.unit
def test_train_rejects_non_sequence_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="training_cases sequence"):
        kernels.train(**{"0": "not-a-sequence", "1": str(tmp_path / "work")})


@pytest.mark.unit
def test_train_dispatches_to_materializer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_write_training_dataset(**kwargs):
        captured["materialize"] = kwargs
        return {
            "layout": {
                "work_dir": tmp_path / "work",
                "nnunet_results": tmp_path / "work" / "nnUNet_results",
                "dataset_folder": "Dataset901_Synthetic",
            },
            "labels_sanitized": False,
        }

    def fake_allocate_dataset_id(work_root: Path) -> int:
        return 901

    def fake_train_model(**kwargs):
        captured["runtime"] = kwargs
        return {"vox_kind": "nnunet_model", "status": "success"}

    monkeypatch.setattr(kernels.mat, "write_training_dataset", fake_write_training_dataset)
    monkeypatch.setattr(kernels.mat, "allocate_dataset_id", fake_allocate_dataset_id)
    monkeypatch.setattr(kernels.runtime, "train_model", fake_train_model)

    training = [["case_1", [np.zeros((2, 2))], np.zeros((2, 2), dtype=np.uint8)]]
    result = kernels.train(**{"0": training, "1": str(tmp_path / "work"), "2": ["T1"]})

    assert result["vox_kind"] == "nnunet_model"
    assert captured["materialize"]["dataset_id"] == 901
    assert captured["runtime"]["modalities"] == ["T1"]
