"""Case parsing and model handles for nnUNet primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from voxlogica.execution_strategy.results import SequenceValue

MODEL_KIND = "nnunet_model"
FILE_ENDING = ".nii.gz"
DEFAULT_LABELS = {"background": 0, "foreground": 1}


def sanitize_case_id(case_id: Any) -> str:
    text = str(case_id).strip()
    if not text:
        raise ValueError("case_id cannot be empty")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)


def as_list(value: Any, *, name: str) -> list[Any]:
    if isinstance(value, SequenceValue):
        return list(value.iter_values())
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError(f"{name} must be a list, tuple, or runtime sequence")


def is_model(value: Any) -> bool:
    return isinstance(value, dict) and value.get("vox_kind") == MODEL_KIND


def normalize_modalities(value: Any) -> list[str]:
    if isinstance(value, str):
        modalities = [value]
    elif isinstance(value, (list, tuple)):
        modalities = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise ValueError("modalities must be a string or list")
    if not modalities:
        raise ValueError("modalities cannot be empty")
    return modalities


@dataclass(frozen=True)
class TrainingCase:
    case_id: str
    file_id: str
    modalities: list[Any]
    label: Any


@dataclass(frozen=True)
class PredictionCase:
    case_id: str
    file_id: str
    modalities: list[Any]


def parse_training_cases(raw: Any, *, modalities: list[str]) -> list[TrainingCase]:
    cases: list[TrainingCase] = []
    seen: set[str] = set()
    for item in as_list(raw, name="training_cases"):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError("training case must be [case_id, modality_volumes, label_volume]")
        case_id, volumes_raw, label = item
        logical_id = str(case_id).strip()
        if not logical_id:
            raise ValueError("case_id cannot be empty")
        volumes = as_list(volumes_raw, name="modality_volumes")
        if len(volumes) != len(modalities):
            raise ValueError(
                f"case {logical_id!r} has {len(volumes)} modality volumes, expected {len(modalities)}"
            )
        file_id = sanitize_case_id(logical_id)
        if file_id in seen:
            raise ValueError(f"duplicate case_id after sanitization: {file_id!r}")
        seen.add(file_id)
        cases.append(TrainingCase(logical_id, file_id, volumes, label))
    if not cases:
        raise ValueError("training_cases cannot be empty")
    return cases


def parse_prediction_cases(raw: Any, *, modalities: list[str]) -> list[PredictionCase]:
    cases: list[PredictionCase] = []
    for item in as_list(raw, name="prediction_cases"):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("prediction case must be [case_id, modality_volumes]")
        case_id, volumes_raw = item
        logical_id = str(case_id).strip()
        if not logical_id:
            raise ValueError("case_id cannot be empty")
        volumes = as_list(volumes_raw, name="modality_volumes")
        if len(volumes) != len(modalities):
            raise ValueError(
                f"case {logical_id!r} has {len(volumes)} modality volumes, expected {len(modalities)}"
            )
        cases.append(PredictionCase(logical_id, sanitize_case_id(logical_id), volumes))
    if not cases:
        raise ValueError("prediction_cases cannot be empty")
    return cases


def infer_modalities(raw: Any) -> list[str]:
    counts: list[int] = []
    for item in as_list(raw, name="training_cases"):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError("training case must be [case_id, modality_volumes, label_volume]")
        counts.append(len(as_list(item[1], name="modality_volumes")))
    if not counts:
        raise ValueError("training_cases cannot be empty")
    first = counts[0]
    if any(count != first for count in counts[1:]):
        raise ValueError("all training cases must have the same number of modality volumes")
    return [f"ch{index}" for index in range(first)]


def build_model(
    *,
    work_root: str,
    dataset_id: int,
    dataset_folder: str,
    configuration: str,
    modalities: list[str],
    trained_folds: list[int],
    trainer_dir: str,
    labels: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "vox_kind": MODEL_KIND,
        "status": "success",
        "work_root": work_root,
        "dataset_id": dataset_id,
        "dataset_folder": dataset_folder,
        "configuration": configuration,
        "modalities": list(modalities),
        "file_ending": FILE_ENDING,
        "trained_folds": list(trained_folds),
        "trainer_dir": trainer_dir,
        "labels": dict(labels or DEFAULT_LABELS),
    }
