from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxlogica.primitives.nnunet import kernels


@pytest.mark.unit
def test_write_case_rejects_a_case_that_is_not_a_triple(tmp_path: Path) -> None:
    layout = {"dataset_dir": tmp_path / "d", "nnunet_raw": tmp_path / "raw"}
    with pytest.raises(ValueError, match="case_id, modality_volumes, label_volume"):
        kernels.write_case(**{"0": layout, "1": "not-a-case", "2": ["T1"]})


@pytest.mark.unit
def test_train_internal_refuses_anything_but_a_prepared_dataset(tmp_path: Path) -> None:
    """The images must not be reachable from here. A caller that passes cases
    instead of a dataset is the mistake this decomposition exists to prevent."""
    training = [["case_1", [np.zeros((2, 2))], np.zeros((2, 2), dtype=np.uint8)]]
    with pytest.raises(ValueError, match="requires a dataset"):
        kernels.train_internal(**{"0": training, "1": ["T1"]})


@pytest.mark.unit
def test_finalize_rejects_two_cases_with_the_same_sanitized_id(tmp_path: Path) -> None:
    """The one check no single case can make, now made from the ids the writes
    returned."""
    layout = {"dataset_dir": tmp_path / "d", "nnunet_raw": tmp_path / "raw"}
    with pytest.raises(ValueError, match="duplicate case_id"):
        kernels.finalize_dataset(**{"0": layout, "1": ["T1"], "2": ["case_1", "case_1"]})


@pytest.mark.unit
def test_train_internal_dispatches_to_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_train_model(**kwargs):
        captured["runtime"] = kwargs
        return {"vox_kind": "nnunet_model", "status": "success"}

    monkeypatch.setattr(kernels.runtime, "train_model", fake_train_model)

    layout = {
        "dataset_dir": str(tmp_path / "work" / "nnUNet_raw" / "Dataset901_Synthetic"),
        "nnunet_raw": str(tmp_path / "work" / "nnUNet_raw"),
        "nnunet_results": str(tmp_path / "work" / "nnUNet_results"),
        "dataset_folder": "Dataset901_Synthetic",
        "dataset_id": 901,
        "dataset_name": "Synthetic",
    }
    result = kernels.train_internal(**{"0": layout, "1": ["T1"]})

    assert result["vox_kind"] == "nnunet_model"
    assert captured["runtime"]["dataset_id"] == 901
    assert captured["runtime"]["modalities"] == ["T1"]
    assert captured["runtime"]["trainer"] == "nnUNetTrainer"


@pytest.mark.unit
def test_train_internal_passes_custom_trainer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        kernels.runtime,
        "train_model",
        lambda **kwargs: captured.update(kwargs) or {"vox_kind": "nnunet_model"},
    )
    layout = {
        "dataset_dir": str(tmp_path / "d"),
        "nnunet_raw": str(tmp_path / "raw"),
        "dataset_id": 901,
        "dataset_name": "Synthetic",
    }
    kernels.train_internal(**{"0": layout, "1": ["T1"], "5": "nnUNetTrainer_10epochs"})

    assert captured["trainer"] == "nnUNetTrainer_10epochs"


@pytest.mark.unit
def test_train_expands_to_one_write_node_per_case() -> None:
    """The memory fix, as a shape.

    `nnunet.train` must NOT be a single node taking every image as an argument:
    that is what made a 309-case run hold 25.7 GB of images and die at 51.4 GB.
    It must be a loop whose body writes one case, so that each image is freed
    when its own write node completes.
    """
    from voxlogica.parser import parse_program_content
    from voxlogica.reducer import reduce_program

    program = """
import "nnunet"
cases = [["a", [1], 2], ["b", [3], 4], ["c", [5], 6]]
print "m" nnunet.train(cases, "/tmp/w", ["intensity"], "2d", 1, "T", "cpu", "nnUNetTrainer")
"""
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()
    operators = [node.operator for node in plan.nodes.values()]

    assert "default.for_loop" in operators, "the per-case writing must be a loop"
    assert operators.count("nnunet.train_internal") == 1
    assert operators.count("nnunet.prepare_dataset") == 1
    assert operators.count("nnunet.finalize_dataset") == 1
    # The write is inside the closure the loop expands at run time, so it is
    # deliberately NOT a static node here.
    assert "nnunet.write_case" not in operators
