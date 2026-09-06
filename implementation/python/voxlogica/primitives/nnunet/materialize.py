"""Write VoxLogicA cases into nnUNet raw and inference folders."""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

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


def nnunet_roots(work_root: Path) -> dict[str, Path]:
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
    # In-process prediction compiles the network too, and a compiled graph that
    # silently does not train is a compiled graph one should not trust to
    # predict either. See nnunet_env() in runtime.py for the measurement that
    # made this the default (0.0 vs 0.854 validation Dice).
    os.environ.setdefault("nnUNet_compile", "f")
    return roots


# The three halves of what used to be one function.
#
# `write_training_dataset` took every case at once and looped over them in
# Python. That loop is the language's own `for`, written a second time and
# written EAGERLY: as an operator argument the whole training set had to be
# resident before the kernel was called, and a 309-case, 4-modality run held
# 25.7 GB of images to write each one once and never touch it again. It was
# killed at 51.4 GB (see
# looping_experiment/results/engine_measurements/2026-09-04-handles-memory-bound).
#
# Split into prepare / one case / finalize, the loop moves into the program,
# where the engine's own `for` streams it: each image is freed when its write
# node completes, because the graph's refcount is then the only thing holding
# it. `write_training_dataset` is kept below, in terms of these three, for
# callers that legitimately have every case in hand already.


def prepare_training_dataset(
    *,
    work_root: Path,
    dataset_id: int,
    dataset_name: str,
) -> dict[str, Any]:
    """Create the empty nnU-Net raw dataset and return where it lives."""
    roots = nnunet_roots(work_root)
    folder = dataset_folder_name(dataset_id, dataset_name)
    dataset_dir = roots["nnunet_raw"] / folder
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    (dataset_dir / "imagesTr").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labelsTr").mkdir(parents=True, exist_ok=True)
    return {**roots, "dataset_id": dataset_id, "dataset_folder": folder,
            "dataset_dir": dataset_dir, "dataset_name": dataset_name}


def write_training_case(layout: dict[str, Any], case: TrainingCase) -> str:
    """Write ONE case into a prepared dataset and return the file id it used.

    Nothing about this case is retained: once this returns, the engine is free
    to drop the images, which is the whole point of doing it one at a time. The
    file id goes back so that the caller holding every id can make the one
    judgement a single case cannot -- that no two of them collide.
    """
    dataset_dir = Path(layout["dataset_dir"])
    images_tr = dataset_dir / "imagesTr"
    labels_tr = dataset_dir / "labelsTr"
    for index, volume in enumerate(case.modalities):
        write_nifti(volume, images_tr / f"{case.file_id}_{index:04d}{FILE_ENDING}")
    if write_label(case.label, labels_tr / f"{case.file_id}{FILE_ENDING}"):
        logger.warning("nnUNet: label of case %s was sanitized", case.logical_id)
    return case.file_id


def finalize_training_dataset(
    *,
    layout: dict[str, Any],
    modalities: list[str],
    file_ids: list[str],
    labels: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Write `dataset.json` and the state file, once every case is on disk.

    `file_ids` is what the per-case writes returned. It is both the training
    count and the collision check that no single case could make; passing it as
    an argument is also what makes this step DEPEND on those writes in the
    graph, rather than merely happening to run after them.
    """
    seen: set[str] = set()
    for file_id in file_ids:
        if file_id in seen:
            raise ValueError(f"duplicate case_id after sanitization: {file_id!r}")
        seen.add(file_id)
    if not file_ids:
        raise ValueError("training_cases cannot be empty")
    num_training = len(file_ids)
    work_root = Path(layout["nnunet_raw"]).parent
    dataset_dir = Path(layout["dataset_dir"])
    dataset_id = int(layout["dataset_id"])
    dataset_name = str(layout["dataset_name"])
    folder = str(layout["dataset_folder"])
    label_defs = labels or DEFAULT_LABELS
    dataset_json = {
        "channel_names": {str(index): name for index, name in enumerate(modalities)},
        "labels": label_defs,
        "numTraining": num_training,
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

    return {"layout": layout, "num_training": num_training}


def write_training_dataset(
    *,
    work_root: Path,
    dataset_id: int,
    dataset_name: str,
    modalities: list[str],
    cases: list[TrainingCase],
    labels: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Prepare, write every case, finalize -- for a caller holding all of them.

    Kept so nothing that already had the whole training set in hand has to
    change. A program should prefer the three steps with a `for` between them:
    this one is as resident as the set it is given.
    """
    layout = prepare_training_dataset(
        work_root=work_root, dataset_id=dataset_id, dataset_name=dataset_name
    )
    file_ids = [write_training_case(layout, case) for case in cases]
    return finalize_training_dataset(
        layout=layout, modalities=modalities, file_ids=file_ids, labels=labels,
    )


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
