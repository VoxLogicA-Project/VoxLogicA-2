from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxlogica.primitives.nnunet import cases, materialize


@pytest.mark.unit
def test_parse_training_cases_pairs_modalities_with_labels() -> None:
    parsed = cases.parse_training_cases(
        [
            ["case_a", [np.zeros((2, 2)), np.ones((2, 2))], np.zeros((2, 2), dtype=np.uint8)],
            ["case_b", [np.zeros((2, 2)), np.ones((2, 2))], np.zeros((2, 2), dtype=np.uint8)],
        ],
        modalities=["T1", "T2"],
    )
    assert len(parsed) == 2
    assert parsed[0].case_id == "case_a"
    assert len(parsed[0].modalities) == 2


@pytest.mark.unit
def test_parse_training_cases_rejects_mismatched_modalities() -> None:
    with pytest.raises(ValueError, match="expected 2"):
        cases.parse_training_cases(
            [["case_a", [np.zeros((2, 2))], np.zeros((2, 2), dtype=np.uint8)]],
            modalities=["T1", "T2"],
        )


@pytest.mark.unit
def test_allocate_dataset_id_reuses_state(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    materialize.save_state(
        work_root,
        {
            "dataset_id": 901,
            "dataset_folder": "Dataset901_Test",
            "dataset_name": "Test",
            "modalities": ["T1"],
            "labels": {"background": 0, "foreground": 1},
        },
    )
    assert materialize.allocate_dataset_id(work_root) == 901


@pytest.mark.unit
def test_write_training_dataset_writes_nnunet_layout(tmp_path: Path) -> None:
    pytest.importorskip("nibabel")
    work_root = tmp_path / "work"
    parsed = cases.parse_training_cases(
        [["patient_1", [np.zeros((2, 2))], np.zeros((2, 2), dtype=np.uint8)]],
        modalities=["T1"],
    )
    result = materialize.write_training_dataset(
        work_root=work_root,
        dataset_id=901,
        dataset_name="Synthetic",
        modalities=["T1"],
        cases=parsed,
    )
    images_tr = result["layout"]["dataset_dir"] / "imagesTr"
    labels_tr = result["layout"]["dataset_dir"] / "labelsTr"
    assert (images_tr / "patient_1_0000.nii.gz").is_file()
    assert (labels_tr / "patient_1.nii.gz").is_file()
    assert materialize.state_path(work_root).is_file()
