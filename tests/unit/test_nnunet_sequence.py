from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxlogica.primitives.nnunet import kernels, manifest, materialize, types


@pytest.mark.unit
def test_parse_training_cases_pairs_modalities_with_labels() -> None:
    cases = types.parse_training_cases(
        [
            ["case_a", [np.zeros((2, 2)), np.ones((2, 2))], np.zeros((2, 2), dtype=np.uint8)],
            ["case_b", [np.zeros((2, 2)), np.ones((2, 2))], np.zeros((2, 2), dtype=np.uint8)],
        ],
        modalities=["T1", "T2"],
    )
    assert len(cases) == 2
    assert cases[0].logical_id == "case_a"
    assert len(cases[0].modality_arrays) == 2


@pytest.mark.unit
def test_parse_training_cases_rejects_mismatched_modalities() -> None:
    with pytest.raises(ValueError, match="expected 2"):
        types.parse_training_cases(
            [["case_a", [np.zeros((2, 2))], np.zeros((2, 2), dtype=np.uint8)]],
            modalities=["T1", "T2"],
        )


@pytest.mark.unit
def test_allocate_dataset_id_reuses_manifest(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    manifest.save_manifest(
        work_root,
        {
            "schema_version": 1,
            "dataset_id": 901,
            "dataset_folder": "Dataset901_Test",
            "dataset_name": "Test",
            "modalities": ["T1"],
            "configuration": "",
            "labels": {"background": 0, "foreground": 1},
            "file_ending": ".nii.gz",
            "cases": {},
            "trained_folds": [],
            "trainer_dir": None,
        },
    )
    assert manifest.allocate_dataset_id(work_root) == 901


@pytest.mark.unit
def test_materialize_training_cases_writes_nnunet_layout(tmp_path: Path) -> None:
    pytest.importorskip("nibabel")
    work_root = tmp_path / "work"
    cases = types.parse_training_cases(
        [["patient_1", [np.zeros((2, 2, 2))], np.zeros((2, 2, 2), dtype=np.uint8)]],
        modalities=["T1"],
    )
    result = materialize.materialize_training_cases(
        work_root=work_root,
        dataset_id=901,
        dataset_name="Synthetic",
        modalities=["T1"],
        labels={"background": 0, "foreground": 1},
        cases=cases,
    )
    images_tr = result["layout"]["imagesTr"]
    labels_tr = result["layout"]["labelsTr"]
    assert (images_tr / "patient_1_0000.nii.gz").is_file()
    assert (labels_tr / "patient_1.nii.gz").is_file()
    assert (work_root / "voxlogica_manifest.json").is_file()


@pytest.mark.unit
def test_train_sequence_dispatches_to_materializer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_materialize_training_cases(**kwargs):
        captured["materialize"] = kwargs
        return {
            "layout": {
                "work_dir": tmp_path / "work",
                "nnunet_results": tmp_path / "work" / "nnUNet_results",
                "padded_name": "Dataset901_Synthetic",
            },
            "labels_sanitized": False,
            "label_value_map": {},
            "manifest_path": str(tmp_path / "work" / "voxlogica_manifest.json"),
        }

    def fake_allocate_dataset_id(work_root: Path, *, preferred: int | None = None) -> int:
        return 901

    def fake_run_training_pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return {
            "vox_kind": "nnunet_model",
            "status": "success",
            "fold_results": [{"fold": 0, "status": "success"}],
        }

    monkeypatch.setattr(kernels.mat, "materialize_training_cases", fake_materialize_training_cases)
    monkeypatch.setattr(kernels, "allocate_dataset_id", fake_allocate_dataset_id)
    monkeypatch.setattr(kernels, "_run_training_pipeline", fake_run_training_pipeline)

    training = [
        ["case_1", [np.zeros((2, 2))], np.zeros((2, 2), dtype=np.uint8)],
    ]
    result = kernels.train(**{"0": training, "1": str(tmp_path / "work"), "2": ["T1"]})

    assert result["vox_kind"] == "nnunet_model"
    assert captured["materialize"]["dataset_id"] == 901
    assert captured["pipeline"]["modalities"] == ["T1"]


@pytest.mark.unit
def test_train_rejects_invalid_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="training_cases sequence or Dask bags"):
        kernels.train(
            **{
                "0": "images",
                "1": "labels",
                "2": ["T1"],
                "3": str(tmp_path / "work"),
            }
        )
