"""Validation and handle types for nnUNet primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from voxlogica.execution_strategy.results import SequenceValue

NNUNET_MODEL_KIND = "nnunet_model"
NNUNET_FILE_ENDING = ".nii.gz"
MANIFEST_FILENAME = "voxlogica_manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def sanitize_case_name(case_id: Any) -> str:
    text = str(case_id).strip()
    if not text:
        raise ValueError("case_id cannot be empty")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)


def iter_sequence(value: Any, *, name: str = "sequence") -> list[Any]:
    if isinstance(value, SequenceValue):
        return list(value.iter_values())
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError(f"{name} must be a list, tuple, or runtime sequence")


def is_model_handle(value: Any) -> bool:
    return isinstance(value, dict) and value.get("vox_kind") == NNUNET_MODEL_KIND


def is_options_mapping(value: Any) -> bool:
    return isinstance(value, dict) and not is_model_handle(value)


@dataclass(frozen=True)
class TrainingCase:
    logical_id: str
    sanitized_id: str
    modality_arrays: list[Any]
    label_array: Any


@dataclass(frozen=True)
class PredictionCase:
    logical_id: str
    sanitized_id: str
    modality_arrays: list[Any]


def parse_training_case(raw: Any, *, modalities: list[str]) -> TrainingCase:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError("training case must be [case_id, modality_volumes, label_volume]")
    case_id, modality_volumes, label_array = raw
    logical_id = str(case_id).strip()
    if not logical_id:
        raise ValueError("case_id cannot be empty")
    volumes = iter_sequence(modality_volumes, name="modality_volumes")
    if len(volumes) != len(modalities):
        raise ValueError(
            f"case {logical_id!r} has {len(volumes)} modality volumes, expected {len(modalities)}"
        )
    return TrainingCase(
        logical_id=logical_id,
        sanitized_id=sanitize_case_name(logical_id),
        modality_arrays=volumes,
        label_array=label_array,
    )


def parse_prediction_case(raw: Any, *, modalities: list[str]) -> PredictionCase:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError("prediction case must be [case_id, modality_volumes]")
    case_id, modality_volumes = raw
    logical_id = str(case_id).strip()
    if not logical_id:
        raise ValueError("case_id cannot be empty")
    volumes = iter_sequence(modality_volumes, name="modality_volumes")
    if len(volumes) != len(modalities):
        raise ValueError(
            f"case {logical_id!r} has {len(volumes)} modality volumes, expected {len(modalities)}"
        )
    return PredictionCase(
        logical_id=logical_id,
        sanitized_id=sanitize_case_name(logical_id),
        modality_arrays=volumes,
    )


def parse_training_cases(raw_cases: Any, *, modalities: list[str]) -> list[TrainingCase]:
    cases = [parse_training_case(item, modalities=modalities) for item in iter_sequence(raw_cases, name="training_cases")]
    if not cases:
        raise ValueError("training_cases cannot be empty")
    seen: set[str] = set()
    for case in cases:
        if case.sanitized_id in seen:
            raise ValueError(f"duplicate case_id after sanitization: {case.sanitized_id!r}")
        seen.add(case.sanitized_id)
    return cases


def parse_prediction_cases(raw_cases: Any, *, modalities: list[str]) -> list[PredictionCase]:
    cases = [
        parse_prediction_case(item, modalities=modalities)
        for item in iter_sequence(raw_cases, name="prediction_cases")
    ]
    if not cases:
        raise ValueError("prediction_cases cannot be empty")
    return cases


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


def infer_modalities(training_cases: list[TrainingCase]) -> list[str]:
    count = len(training_cases[0].modality_arrays)
    for case in training_cases[1:]:
        if len(case.modality_arrays) != count:
            raise ValueError("all training cases must have the same number of modality volumes")
    return [f"ch{index}" for index in range(count)]


def normalize_labels(value: Any | None) -> dict[str, int]:
    if value is None:
        return {"background": 0, "foreground": 1}
    if not isinstance(value, dict):
        raise ValueError("labels option must be a mapping")
    labels = {str(name): int(label) for name, label in value.items()}
    if labels.get("background", 0) != 0:
        raise ValueError("labels must include background: 0")
    return labels


def build_model_handle(
    *,
    work_root: str,
    dataset_id: int,
    dataset_folder: str,
    dataset_name: str,
    configuration: str,
    modalities: list[str],
    nfolds: int,
    trained_folds: list[int],
    trainer_dir: str,
    labels: dict[str, int],
    manifest_path: str,
    file_ending: str = NNUNET_FILE_ENDING,
    status: str = "success",
) -> dict[str, Any]:
    return {
        "vox_kind": NNUNET_MODEL_KIND,
        "status": status,
        "work_root": work_root,
        "dataset_id": dataset_id,
        "dataset_folder": dataset_folder,
        "dataset_name": dataset_name,
        "configuration": configuration,
        "modalities": list(modalities),
        "file_ending": file_ending,
        "nfolds": nfolds,
        "trained_folds": list(trained_folds),
        "trainer_dir": trainer_dir,
        "labels": dict(labels),
        "materialization_manifest": manifest_path,
        # Legacy-compatible aliases (deprecated).
        "model_path": trainer_dir,
        "work_dir": work_root,
    }
