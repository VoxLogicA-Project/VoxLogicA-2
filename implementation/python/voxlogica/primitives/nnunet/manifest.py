"""Work-root manifest for nnUNet dataset and model metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from voxlogica.primitives.nnunet.types import MANIFEST_FILENAME, MANIFEST_SCHEMA_VERSION

_DATASET_DIR_RE = re.compile(r"^Dataset(\d{1,3})_.+$")


def manifest_path(work_root: Path) -> Path:
    return work_root / MANIFEST_FILENAME


def load_manifest(work_root: Path) -> dict[str, Any] | None:
    path = manifest_path(work_root)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid manifest at {path}")
    return payload


def save_manifest(work_root: Path, payload: dict[str, Any]) -> Path:
    work_root.mkdir(parents=True, exist_ok=True)
    path = manifest_path(work_root)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def dataset_folder_name(dataset_id: int, dataset_name: str) -> str:
    return f"Dataset{str(dataset_id).zfill(3)}_{dataset_name}"


def _scan_existing_dataset_ids(nnunet_raw: Path) -> list[int]:
    ids: list[int] = []
    if not nnunet_raw.is_dir():
        return ids
    for entry in nnunet_raw.iterdir():
        if not entry.is_dir():
            continue
        match = _DATASET_DIR_RE.match(entry.name)
        if match:
            ids.append(int(match.group(1)))
    return ids


def allocate_dataset_id(work_root: Path, *, preferred: int | None = None) -> int:
    existing = load_manifest(work_root)
    if existing is not None and "dataset_id" in existing:
        return int(existing["dataset_id"])

    nnunet_raw = work_root / "nnUNet_raw"
    used = set(_scan_existing_dataset_ids(nnunet_raw))
    if preferred is not None and preferred not in used:
        return preferred
    start = max([900, *used, 0]) + 1 if used else 900
    while start in used:
        start += 1
    return start


def build_manifest_payload(
    *,
    dataset_id: int,
    dataset_name: str,
    modalities: list[str],
    configuration: str,
    labels: dict[str, int],
    cases: dict[str, dict[str, Any]],
    trained_folds: list[int],
    trainer_dir: str | None,
    file_ending: str,
) -> dict[str, Any]:
    dataset_folder = dataset_folder_name(dataset_id, dataset_name)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_folder": dataset_folder,
        "dataset_name": dataset_name,
        "modalities": list(modalities),
        "configuration": configuration,
        "labels": dict(labels),
        "file_ending": file_ending,
        "cases": cases,
        "trained_folds": list(trained_folds),
        "trainer_dir": trainer_dir,
    }


def case_manifest_entry(
    *,
    logical_id: str,
    sanitized_id: str,
    channel_filenames: list[str],
    label_filename: str,
) -> dict[str, Any]:
    return {
        "logical_id": logical_id,
        "sanitized_id": sanitized_id,
        "channels": channel_filenames,
        "label": label_filename,
    }
