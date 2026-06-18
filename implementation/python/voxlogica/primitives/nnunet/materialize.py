"""Write VoxLogicA cases into nnUNet raw and inference folders."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from voxlogica.primitives.nnunet.cases import (
    DEFAULT_LABELS,
    FILE_ENDING,
    PredictionCase,
    TrainingCase,
)
from voxlogica.primitives.nnunet.io import write_label, write_nifti

STATE_FILE = "voxlogica_manifest.json"
_DATASET_DIR_RE = re.compile(r"^Dataset(\d{1,3})_.+$")


def dataset_folder_name(dataset_id: int, dataset_name: str) -> str:
    return f"Dataset{str(dataset_id).zfill(3)}_{dataset_name}"


def state_path(work_root: Path) -> Path:
    return work_root / STATE_FILE


def load_state(work_root: Path) -> dict[str, Any] | None:
    path = state_path(work_root)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid state file at {path}")
    return payload


def save_state(work_root: Path, payload: dict[str, Any]) -> None:
    work_root.mkdir(parents=True, exist_ok=True)
    state_path(work_root).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def allocate_dataset_id(work_root: Path) -> int:
    state = load_state(work_root)
    if state is not None and "dataset_id" in state:
        return int(state["dataset_id"])

    used: set[int] = set()
    raw_root = work_root / "nnUNet_raw"
    if raw_root.is_dir():
        for entry in raw_root.iterdir():
            if entry.is_dir() and (match := _DATASET_DIR_RE.match(entry.name)):
                used.add(int(match.group(1)))

    dataset_id = 900
    while dataset_id in used:
        dataset_id += 1
    return dataset_id


def _set_nnunet_env(work_root: Path) -> dict[str, Path]:
    roots = {
        "work_dir": work_root,
        "nnunet_raw": work_root / "nnUNet_raw",
        "nnunet_preprocessed": work_root / "nnUNet_preprocessed",
        "nnunet_results": work_root / "nnUNet_results",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    os.environ["nnUNet_raw"] = str(roots["nnunet_raw"])
    os.environ["nnUNet_preprocessed"] = str(roots["nnunet_preprocessed"])
    os.environ["nnUNet_results"] = str(roots["nnunet_results"])
    return roots


def write_training_dataset(
    *,
    work_root: Path,
    dataset_id: int,
    dataset_name: str,
    modalities: list[str],
    cases: list[TrainingCase],
    labels: dict[str, int] | None = None,
) -> dict[str, Any]:
    roots = _set_nnunet_env(work_root)
    folder = dataset_folder_name(dataset_id, dataset_name)
    dataset_dir = roots["nnunet_raw"] / folder
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    images_tr = dataset_dir / "imagesTr"
    labels_tr = dataset_dir / "labelsTr"
    images_tr.mkdir(parents=True, exist_ok=True)
    labels_tr.mkdir(parents=True, exist_ok=True)

    label_defs = labels or DEFAULT_LABELS
    labels_sanitized = False
    for case in cases:
        for index, volume in enumerate(case.modalities):
            write_nifti(volume, images_tr / f"{case.file_id}_{index:04d}{FILE_ENDING}")
        labels_sanitized = (
            write_label(case.label, labels_tr / f"{case.file_id}{FILE_ENDING}") or labels_sanitized
        )

    dataset_json = {
        "channel_names": {str(index): name for index, name in enumerate(modalities)},
        "labels": label_defs,
        "numTraining": len(cases),
        "file_ending": FILE_ENDING,
        "dataset_name": dataset_name,
    }
    (dataset_dir / "dataset.json").write_text(json.dumps(dataset_json, indent=2), encoding="utf-8")

    save_state(
        work_root,
        {
            "dataset_id": dataset_id,
            "dataset_folder": folder,
            "dataset_name": dataset_name,
            "modalities": modalities,
            "labels": label_defs,
        },
    )

    layout = {**roots, "dataset_id": dataset_id, "dataset_folder": folder, "dataset_dir": dataset_dir}
    return {"layout": layout, "labels_sanitized": labels_sanitized}


def write_prediction_inputs(
    *,
    work_root: Path,
    cases: list[PredictionCase],
    file_ending: str = FILE_ENDING,
    run_id: str | None = None,
) -> Path:
    run = run_id or uuid.uuid4().hex[:12]
    inference_root = work_root / "materialized" / "inference" / run
    if inference_root.exists():
        shutil.rmtree(inference_root)
    inference_root.mkdir(parents=True, exist_ok=True)

    for case in cases:
        for index, volume in enumerate(case.modalities):
            write_nifti(volume, inference_root / f"{case.file_id}_{index:04d}{file_ending}")
    return inference_root
