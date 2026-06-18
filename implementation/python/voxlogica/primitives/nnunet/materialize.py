"""Materialize VoxLogicA cases into nnUNet raw and inference folders."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from voxlogica.primitives.nnunet.manifest import (
    build_manifest_payload,
    case_manifest_entry,
    dataset_folder_name,
    manifest_path,
    save_manifest,
)
from voxlogica.primitives.nnunet.types import NNUNET_FILE_ENDING, PredictionCase, TrainingCase

NNUNET_MIN_DEPTH = 32


def prepare_runtime_roots(work_dir: Path) -> dict[str, Path]:
    nnunet_raw = work_dir / "nnUNet_raw"
    nnunet_preprocessed = work_dir / "nnUNet_preprocessed"
    nnunet_results = work_dir / "nnUNet_results"
    for directory in (nnunet_raw, nnunet_preprocessed, nnunet_results):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ["nnUNet_raw"] = str(nnunet_raw)
    os.environ["nnUNet_preprocessed"] = str(nnunet_preprocessed)
    os.environ["nnUNet_results"] = str(nnunet_results)

    return {
        "work_dir": work_dir,
        "nnunet_raw": nnunet_raw,
        "nnunet_preprocessed": nnunet_preprocessed,
        "nnunet_results": nnunet_results,
    }


def dataset_layout(work_root: Path, dataset_id: int, dataset_name: str) -> dict[str, Path | str]:
    runtime = prepare_runtime_roots(work_root)
    padded_name = dataset_folder_name(dataset_id, dataset_name)
    dataset_dir = runtime["nnunet_raw"] / padded_name
    images_tr = dataset_dir / "imagesTr"
    labels_tr = dataset_dir / "labelsTr"
    images_ts = dataset_dir / "imagesTs"
    for directory in (images_tr, labels_tr, images_ts):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        **runtime,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "padded_name": padded_name,
        "dataset_dir": dataset_dir,
        "imagesTr": images_tr,
        "labelsTr": labels_tr,
        "imagesTs": images_ts,
    }


def _load_array_io_modules() -> tuple[Any, Any]:
    try:
        import nibabel as nib  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"nnUNet materialization requires nibabel and numpy: {exc}") from exc
    return np, nib


def _array_from_value(value: Any) -> tuple[Any, Any]:
    np, _nib = _load_array_io_modules()
    try:
        import SimpleITK as sitk  # type: ignore

        if isinstance(value, sitk.Image):
            return sitk.GetArrayFromImage(value), value
    except Exception:  # noqa: BLE001
        pass
    return np.asarray(value), None


def _to_volume_array(value: Any) -> Any:
    """Promote arrays to 3D when a volumetric nnUNet configuration needs it."""
    np, _nib = _load_array_io_modules()
    array, _reference = _array_from_value(value)
    if array.ndim == 2:
        return np.stack([array] * NNUNET_MIN_DEPTH, axis=0)
    if array.ndim == 3:
        if array.shape[0] < NNUNET_MIN_DEPTH:
            repeats = int(np.ceil(NNUNET_MIN_DEPTH / array.shape[0]))
            array = np.repeat(array, repeats, axis=0)[:NNUNET_MIN_DEPTH]
        return array
    raise ValueError(f"expected 2D or 3D image data, got shape {array.shape}")


def write_nifti(array: Any, destination: Path, *, force_volume: bool = False) -> None:
    np, nib = _load_array_io_modules()
    import SimpleITK as sitk  # type: ignore

    destination.parent.mkdir(parents=True, exist_ok=True)
    volume, reference = _array_from_value(array)
    if not force_volume and volume.ndim == 2:
        image = sitk.GetImageFromArray(volume.astype(np.float32))
        if reference is not None:
            image.CopyInformation(reference)
        else:
            image.SetSpacing((1.0, 1.0))
            image.SetOrigin((0.0, 0.0))
        sitk.WriteImage(image, str(destination))
        return

    stacked = _to_volume_array(array) if volume.ndim == 2 else volume
    image = nib.Nifti1Image(stacked, np.eye(4))
    nib.save(image, str(destination))


def write_dataset_json(
    dataset_dir: Path,
    *,
    modalities: list[str],
    dataset_name: str,
    num_training: int,
    labels: dict[str, int],
    file_ending: str = NNUNET_FILE_ENDING,
) -> None:
    dataset_json = {
        "channel_names": {str(index): modality for index, modality in enumerate(modalities)},
        "labels": labels,
        "numTraining": num_training,
        "file_ending": file_ending,
        "dataset_name": dataset_name,
    }
    (dataset_dir / "dataset.json").write_text(json.dumps(dataset_json, indent=2), encoding="utf-8")


def _sanitize_label_array(label_array: Any) -> tuple[Any, bool, list[int]]:
    np, _nib = _load_array_io_modules()
    label_arr, reference = _array_from_value(label_array)
    unique_vals = sorted({int(x) for x in np.unique(label_arr).tolist()})
    sanitized = False
    if any(value not in (0, 1) for value in unique_vals):
        label_arr = (label_arr > 0).astype("uint8")
        sanitized = True
    if reference is not None:
        import SimpleITK as sitk  # type: ignore

        label_image = sitk.GetImageFromArray(label_arr.astype(np.uint8))
        label_image.CopyInformation(reference)
        return label_image, sanitized, unique_vals
    return label_arr, sanitized, unique_vals


def materialize_training_cases(
    *,
    work_root: Path,
    dataset_id: int,
    dataset_name: str,
    modalities: list[str],
    labels: dict[str, int],
    cases: list[TrainingCase],
    file_ending: str = NNUNET_FILE_ENDING,
) -> dict[str, Any]:
    layout = dataset_layout(work_root, dataset_id, dataset_name)
    case_manifest: dict[str, dict[str, Any]] = {}
    labels_sanitized = False
    label_value_map: dict[str, list[int]] = {}

    for case in cases:
        channel_filenames: list[str] = []
        for mod_idx, array in enumerate(case.modality_arrays):
            filename = f"{case.sanitized_id}_{mod_idx:04d}{file_ending}"
            channel_filenames.append(filename)
            write_nifti(array, layout["imagesTr"] / filename)

        label_filename = f"{case.sanitized_id}{file_ending}"
        label_arr, case_sanitized, unique_vals = _sanitize_label_array(case.label_array)
        labels_sanitized = labels_sanitized or case_sanitized
        label_value_map[label_filename] = unique_vals
        write_nifti(label_arr, layout["labelsTr"] / label_filename)

        case_manifest[case.logical_id] = case_manifest_entry(
            logical_id=case.logical_id,
            sanitized_id=case.sanitized_id,
            channel_filenames=channel_filenames,
            label_filename=label_filename,
        )

    write_dataset_json(
        layout["dataset_dir"],
        modalities=modalities,
        dataset_name=dataset_name,
        num_training=len(cases),
        labels=labels,
        file_ending=file_ending,
    )

    manifest_payload = build_manifest_payload(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        modalities=modalities,
        configuration="",
        labels=labels,
        cases=case_manifest,
        trained_folds=[],
        trainer_dir=None,
        file_ending=file_ending,
    )
    save_manifest(work_root, manifest_payload)

    return {
        "layout": layout,
        "labels_sanitized": labels_sanitized,
        "label_value_map": label_value_map,
        "case_manifest": case_manifest,
        "manifest_path": str(manifest_path(work_root)),
    }


def materialize_prediction_cases(
    *,
    work_root: Path,
    model_handle: dict[str, Any],
    cases: list[PredictionCase],
    run_id: str,
) -> Path:
    file_ending = str(model_handle.get("file_ending", NNUNET_FILE_ENDING))
    inference_root = work_root / "materialized" / "inference" / run_id
    if inference_root.exists():
        shutil.rmtree(inference_root)
    inference_root.mkdir(parents=True, exist_ok=True)

    for case in cases:
        for mod_idx, array in enumerate(case.modality_arrays):
            filename = f"{case.sanitized_id}_{mod_idx:04d}{file_ending}"
            write_nifti(array, inference_root / filename)
    return inference_root
