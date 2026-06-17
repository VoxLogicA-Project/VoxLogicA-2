"""nnUNet primitives for VoxLogicA-2."""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.nnunet import materialize as mat
from voxlogica.primitives.nnunet import pipeline as pipe
from voxlogica.primitives.nnunet.manifest import allocate_dataset_id, dataset_folder_name
from voxlogica.primitives.nnunet.types import (
    NNUNET_FILE_ENDING,
    is_model_handle,
    iter_sequence,
    normalize_labels,
    normalize_modalities,
    parse_prediction_cases,
    parse_training_cases,
    sanitize_case_name,
)

logger = logging.getLogger(__name__)

_NNUNET_FILE_ENDING = NNUNET_FILE_ENDING


def _coerce_int(name: str, value: Any) -> int:
    try:
        return int(float(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} must be int-like: {value!r}: {exc}") from exc


def _coerce_str(name: str, value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def _is_bag(value: Any) -> bool:
    return callable(getattr(value, "compute", None))


def _is_training_sequence(value: Any) -> bool:
    if isinstance(value, str):
        return False
    try:
        iter_sequence(value, name="training_cases")
        return True
    except ValueError:
        return False


def _get_nnunet_env() -> dict[str, str]:
    return pipe.get_nnunet_env()


def _get_nnunet_command_path(command_name: str) -> str:
    return pipe.get_nnunet_command_path(command_name)


def _normalize_modalities(value: Any) -> List[str]:
    return normalize_modalities(value)


def _require_nnunet_installation() -> None:
    pipe.require_nnunet_installation()


def _prepare_runtime_roots(work_dir: Path) -> dict[str, Any]:
    return mat.prepare_runtime_roots(work_dir)


def _dataset_names(dataset_id: int, dataset_name: str) -> dict[str, str]:
    padded_name = dataset_folder_name(dataset_id, dataset_name)
    return {
        "padded_id": str(dataset_id).zfill(3),
        "padded_name": padded_name,
        "unpadded_name": f"Dataset{dataset_id}_{dataset_name}",
    }


def _ensure_dataset_name_consistency(
    nnunet_raw: Path,
    dataset_id: int,
    dataset_name: str,
) -> None:
    names = _dataset_names(dataset_id, dataset_name)
    conflict_dirs = sorted(
        {
            path.name
            for path in nnunet_raw.glob(f"Dataset{names['padded_id']}_*")
            if path.is_dir()
        }
    )
    if len(conflict_dirs) > 1 and names["padded_name"] not in conflict_dirs:
        raise ValueError(
            (
                "Multiple dataset names already exist for dataset id "
                f"{dataset_id} (found: {conflict_dirs}). Either:\n"
                f" - remove conflicting directories under {nnunet_raw.parent}\n"
                " - choose a different dataset id"
            )
        )


def _dataset_layout(
    work_dir: Path,
    dataset_id: int,
    dataset_name: str,
) -> dict[str, Path | str]:
    layout = mat.dataset_layout(work_dir, dataset_id, dataset_name)
    _ensure_dataset_name_consistency(layout["nnunet_raw"], dataset_id, dataset_name)
    names = _dataset_names(dataset_id, dataset_name)
    layout["unpadded_name"] = names["unpadded_name"]
    return layout


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src, dst)
    except Exception:  # noqa: BLE001
        shutil.copy2(src, dst)


def _sanitize_case_name(case_id: Any) -> str:
    return sanitize_case_name(case_id)


def _load_array_io_modules() -> tuple[Any, Any]:
    try:
        import nibabel as nib  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Bag-based nnUNet training requires nibabel and numpy: {exc}") from exc
    return np, nib


def _load_simpleitk() -> Any:
    try:
        import SimpleITK as sitk  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"train_directory requires SimpleITK: {exc}") from exc
    return sitk


def _write_nifti(array: Any, destination: Path) -> None:
    mat.write_nifti(array, destination)


def _collect_bag_records(bag: Any, name: str) -> list[Any]:
    compute = getattr(bag, "compute", None)
    if not callable(compute):
        raise ValueError(f"{name} must be a Dask bag-like object exposing compute()")

    records = list(compute())
    if not records:
        raise ValueError(f"{name} cannot be empty")
    return records


def _materialize_bag_dataset(
    *,
    images_bag: Any,
    labels_bag: Any,
    modalities: list[str],
    work_dir: Path,
    dataset_id: int,
    dataset_name: str,
) -> dict[str, Any]:
    layout = _dataset_layout(work_dir, dataset_id, dataset_name)
    image_records = _collect_bag_records(images_bag, "images_bag")
    label_records = _collect_bag_records(labels_bag, "labels_bag")

    images_by_case: dict[str, dict[str, Any]] = {}
    for record in image_records:
        if not isinstance(record, (list, tuple)) or len(record) != 3:
            raise ValueError(
                "images_bag elements must have format (case_id, modality, numpy_array)"
            )
        case_id, modality, array = record
        case_name = _sanitize_case_name(case_id)
        images_by_case.setdefault(case_name, {})[str(modality)] = array

    label_value_map: dict[str, list[int]] = {}
    labels_sanitized = False
    training_cases = 0

    for record in label_records:
        if not isinstance(record, (list, tuple)) or len(record) != 2:
            raise ValueError("labels_bag elements must have format (case_id, numpy_array)")
        case_id, label_array = record
        case_name = _sanitize_case_name(case_id)
        if case_name not in images_by_case:
            logger.warning("Skipping label for case without images: %s", case_name)
            continue

        case_images = images_by_case[case_name]
        for mod_idx, modality in enumerate(modalities):
            if modality not in case_images:
                raise ValueError(
                    f"Missing modality {modality!r} for case {case_name!r} in images_bag"
                )
            image_path = layout["imagesTr"] / f"{case_name}_{mod_idx:04d}{_NNUNET_FILE_ENDING}"
            _write_nifti(case_images[modality], image_path)

        np, _nib = _load_array_io_modules()
        label_arr = np.asarray(label_array)
        unique_vals = sorted({int(x) for x in np.unique(label_arr).tolist()})
        label_value_map[f"{case_name}{_NNUNET_FILE_ENDING}"] = unique_vals
        if any(value not in (0, 1) for value in unique_vals):
            label_arr = (label_arr > 0).astype("uint8")
            labels_sanitized = True

        label_path = layout["labelsTr"] / f"{case_name}{_NNUNET_FILE_ENDING}"
        _write_nifti(label_arr, label_path)
        training_cases += 1

    if training_cases == 0:
        raise ValueError("No overlapping training cases found between images_bag and labels_bag")

    _write_dataset_json(layout["dataset_dir"], modalities, dataset_name, training_cases)
    return {
        "layout": layout,
        "labels_sanitized": labels_sanitized,
        "label_value_map": label_value_map,
        "manifest_path": str(mat.manifest_path(work_dir)),
    }


def _sanitize_directory_labels(
    *,
    label_files: list[Path],
    work_dir: Path,
) -> tuple[list[Path], bool, dict[str, list[int]]]:
    sitk = _load_simpleitk()
    sanitized_dir = work_dir / "sanitized_labels"
    sanitized_dir.mkdir(parents=True, exist_ok=True)

    sanitized_used = False
    label_value_map: dict[str, list[int]] = {}
    rewritten_files: list[Path] = []

    for label_file in label_files:
        try:
            image = sitk.ReadImage(str(label_file))
            array = sitk.GetArrayFromImage(image)
            unique_vals = sorted({int(value) for value in set(array.flatten())})  # type: ignore[arg-type]
            label_value_map[label_file.name] = unique_vals
            if any(value not in (0, 1) for value in unique_vals):
                sanitized_used = True
                out_array = (array > 0.5).astype("uint8") if array.dtype.kind in ("f", "d") else (array > 0).astype("uint8")
                out_image = sitk.GetImageFromArray(out_array)
                out_image.CopyInformation(image)
                sanitized_path = sanitized_dir / label_file.name
                sitk.WriteImage(out_image, str(sanitized_path))
                rewritten_files.append(sanitized_path)
                continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("Label inspection failed for %s: %s", label_file, exc)
        rewritten_files.append(label_file)

    return rewritten_files, sanitized_used, label_value_map


def _materialize_directory_dataset(
    *,
    images_dir: Path,
    labels_dir: Path,
    modalities: list[str],
    work_dir: Path,
    dataset_id: int,
    dataset_name: str,
) -> dict[str, Any]:
    layout = _dataset_layout(work_dir, dataset_id, dataset_name)
    label_files = sorted(path for path in labels_dir.glob("*.nii*") if path.is_file())
    if not label_files:
        raise ValueError(f"No label files in {labels_dir}")

    label_files, labels_sanitized, label_value_map = _sanitize_directory_labels(
        label_files=label_files,
        work_dir=work_dir,
    )

    for label_file in label_files:
        case_name = label_file.name.split(".")[0]
        for mod_idx, _modality in enumerate(modalities):
            modality_suffix = f"{mod_idx:04d}"
            matches = list(images_dir.glob(f"{case_name}_{modality_suffix}*"))
            if not matches:
                logger.warning("Missing modality %s for %s", modality_suffix, case_name)
                continue
            src = matches[0]
            dst = layout["imagesTr"] / f"{case_name}_{modality_suffix}{''.join(src.suffixes) or _NNUNET_FILE_ENDING}"
            _link_or_copy(src, dst)

        _link_or_copy(
            label_file,
            layout["labelsTr"] / f"{case_name}{''.join(label_file.suffixes)}",
        )

    _write_dataset_json(layout["dataset_dir"], modalities, dataset_name, len(label_files))
    return {
        "layout": layout,
        "labels_sanitized": labels_sanitized,
        "label_value_map": label_value_map,
        "manifest_path": str(mat.manifest_path(work_dir)),
    }


def _write_dataset_json(
    dataset_dir: Path,
    modalities: list[str],
    dataset_name: str,
    num_training: int,
) -> None:
    mat.write_dataset_json(
        dataset_dir,
        modalities=modalities,
        dataset_name=dataset_name,
        num_training=num_training,
        labels={"background": 0, "label_1": 1},
    )


def _run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    step_name: str,
) -> Any:
    return pipe.run_subprocess(command, cwd=cwd, env=env, step_name=step_name)


def _run_training_pipeline(
    *,
    layout: dict[str, Any],
    dataset_id: int,
    configuration: str,
    nfolds: int,
    device: str,
    labels_sanitized: bool,
    label_value_map: dict[str, list[int]],
    dataset_name: str | None = None,
    modalities: list[str] | None = None,
    manifest_path_value: str | None = None,
    labels: dict[str, int] | None = None,
) -> dict[str, Any]:
    resolved_name = dataset_name or str(layout.get("dataset_name") or layout["padded_name"].split("_", 1)[1])
    resolved_modalities = modalities or ["ch0"]
    resolved_manifest = manifest_path_value or str(mat.manifest_path(Path(layout["work_dir"])))
    resolved_labels = labels or {"background": 0, "foreground": 1}

    handle = pipe.run_training_pipeline(
        layout=layout,
        dataset_id=dataset_id,
        dataset_name=resolved_name,
        configuration=configuration,
        nfolds=nfolds,
        device=device,
        labels=resolved_labels,
        labels_sanitized=labels_sanitized,
        label_value_map=label_value_map,
        manifest_path_value=resolved_manifest,
        modalities=resolved_modalities,
    )
    handle["trained_folds"] = [item["fold"] for item in handle.get("fold_results", [])]
    return handle


def _train_from_sequence(**kwargs: Any) -> dict[str, Any]:
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("sequence train requires keys 0..1 (training_cases, work_root)")

    raw_cases = kwargs["0"]
    work_root = Path(_coerce_str("work_root", kwargs["1"]))
    modalities_value = kwargs.get("2")
    configuration = _coerce_str("configuration", kwargs.get("3", "3d_fullres"))
    nfolds = _coerce_int("nfolds", kwargs.get("4", 5))
    dataset_name = _coerce_str("dataset_name", kwargs.get("5", "VoxLogicADataset"))
    device = _coerce_str("device", kwargs.get("6", "gpu")).lower()
    labels = normalize_labels(kwargs.get("7"))

    if nfolds <= 0:
        raise ValueError("nfolds must be >= 1")

    if modalities_value is None:
        preview_cases = [
            parse_training_case_preview(item)
            for item in iter_sequence(raw_cases, name="training_cases")
        ]
        if not preview_cases:
            raise ValueError("training_cases cannot be empty")
        count = len(preview_cases[0])
        for volumes in preview_cases[1:]:
            if len(volumes) != count:
                raise ValueError("all training cases must have the same number of modality volumes")
        modalities = [f"ch{index}" for index in range(count)]
        training_cases = parse_training_cases(raw_cases, modalities=modalities)
    else:
        modalities = _normalize_modalities(modalities_value)
        training_cases = parse_training_cases(raw_cases, modalities=modalities)

    dataset_id = allocate_dataset_id(work_root)
    materialized = mat.materialize_training_cases(
        work_root=work_root,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        modalities=modalities,
        labels=labels,
        cases=training_cases,
    )
    return _run_training_pipeline(
        layout=materialized["layout"],
        dataset_id=dataset_id,
        configuration=configuration,
        nfolds=nfolds,
        device=device,
        labels_sanitized=materialized["labels_sanitized"],
        label_value_map=materialized["label_value_map"],
        dataset_name=dataset_name,
        modalities=modalities,
        manifest_path_value=materialized.get("manifest_path") or str(mat.manifest_path(work_dir)),
        labels=labels,
    )


def parse_training_case_preview(raw: Any) -> list[Any]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError("training case must be [case_id, modality_volumes, label_volume]")
    return iter_sequence(raw[1], name="modality_volumes")


def _train_from_bags(**kwargs: Any) -> dict[str, Any]:
    for key in ("0", "1", "2", "3"):
        if key not in kwargs:
            raise ValueError(
                "train requires keys 0..3 (images_bag, labels_bag, modalities, work_dir)"
            )

    images_bag = kwargs["0"]
    labels_bag = kwargs["1"]
    modalities = _normalize_modalities(kwargs["2"])
    work_dir = Path(_coerce_str("work_dir", kwargs["3"]))
    dataset_id = _coerce_int("dataset_id", kwargs.get("4", 1))
    dataset_name = _coerce_str("dataset_name", kwargs.get("5", "VoxLogicADataset"))
    configuration = _coerce_str("configuration", kwargs.get("6", "3d_fullres"))
    nfolds = _coerce_int("nfolds", kwargs.get("7", 5))
    if nfolds <= 0:
        raise ValueError("nfolds must be >= 1")

    materialized = _materialize_bag_dataset(
        images_bag=images_bag,
        labels_bag=labels_bag,
        modalities=modalities,
        work_dir=work_dir,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    )
    result = _run_training_pipeline(
        layout=materialized["layout"],
        dataset_id=dataset_id,
        configuration=configuration,
        nfolds=nfolds,
        device="gpu",
        labels_sanitized=materialized["labels_sanitized"],
        label_value_map=materialized["label_value_map"],
        dataset_name=dataset_name,
        modalities=modalities,
        manifest_path_value=materialized.get("manifest_path") or str(mat.manifest_path(work_dir)),
    )
    if "trained_folds" not in result and "fold_results" in result:
        result["trained_folds"] = [item["fold"] for item in result["fold_results"]]
    return result


def env_check(**_kwargs: Any) -> dict[str, Any]:
    out: Dict[str, Any] = {
        "torch_available": False,
        "torch_version": None,
        "nnunetv2_available": False,
        "nnunetv2_version": None,
        "issues": [],
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    try:
        try:
            import torch  # type: ignore

            out["torch_available"] = True
            out["torch_version"] = getattr(torch, "__version__", "unknown")
        except Exception as exc:  # noqa: BLE001
            out["issues"].append(f"torch: {exc}")
        try:
            spec = importlib.util.find_spec("nnunetv2")
            if spec is not None:
                import nnunetv2 as nnunet_module  # type: ignore

                out["nnunetv2_available"] = True
                out["nnunetv2_version"] = getattr(nnunet_module, "__version__", "unknown")
            else:
                out["issues"].append("nnunetv2: not found")
        except Exception as exc:  # noqa: BLE001
            out["issues"].append(f"nnunetv2: {exc}")
    except Exception as exc:  # noqa: BLE001
        out["issues"].append(f"unexpected: {exc}")
    out["ready"] = out["torch_available"] and out["nnunetv2_available"]
    return out


def train(**kwargs: Any) -> dict[str, Any]:
    """Train nnUNet from a case sequence or legacy Dask bags."""
    try:
        if _is_bag(kwargs.get("0")) and _is_bag(kwargs.get("1")):
            return _train_from_bags(**kwargs)
        if _is_training_sequence(kwargs.get("0")):
            return _train_from_sequence(**kwargs)
        if _is_bag(kwargs.get("0")):
            raise ValueError("labels_bag must be a Dask bag-like object exposing compute()")
        raise ValueError(
            "train requires a training_cases sequence or Dask bags (images_bag, labels_bag)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet training failed: %s", exc)
        raise ValueError(f"nnUNet training failed: {exc}") from exc


def _predict_from_handle(**kwargs: Any) -> dict[str, Any]:
    model = kwargs["0"]
    if not is_model_handle(model):
        raise ValueError("predict model handle must include vox_kind=nnunet_model")
    if "1" not in kwargs:
        raise ValueError("predict requires prediction_cases as argument 1")

    cases = parse_prediction_cases(kwargs["1"], modalities=list(model["modalities"]))
    work_root = Path(model["work_root"])
    run_id = uuid.uuid4().hex[:12]
    input_dir = mat.materialize_prediction_cases(
        work_root=work_root,
        model_handle=model,
        cases=cases,
        run_id=run_id,
    )
    output_subdir = _coerce_str("output_subdir", kwargs.get("2", "predictions"))
    output_dir = work_root / "materialized" / output_subdir / run_id
    folds = kwargs.get("3")
    fold_list = None
    if folds is not None:
        fold_list = [int(fold) for fold in iter_sequence(folds, name="folds")]
    save_probabilities = bool(kwargs.get("4", False))
    return pipe.run_prediction_pipeline(
        model_handle=model,
        input_dir=input_dir,
        output_dir=output_dir,
        folds=fold_list,
        save_probabilities=save_probabilities,
    )


def _predict_legacy(**kwargs: Any) -> dict[str, Any]:
    for key in ("0", "1", "2"):
        if key not in kwargs:
            raise ValueError(
                "predict requires keys 0..2 (input_images, model_path, output_dir)"
            )

    input_images = Path(str(kwargs["0"]))
    model_path = Path(str(kwargs["1"]))
    output_dir = Path(str(kwargs["2"]))
    configuration = _coerce_str("configuration", kwargs.get("3", "3d_fullres"))
    folds = kwargs.get("4")
    save_probabilities = bool(kwargs.get("5", False))

    _require_nnunet_installation()
    if not model_path.exists():
        raise ValueError(f"model path not found: {model_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    match = re.search(r"Dataset(\d{1,3})_", model_path.name)
    if not match:
        raise ValueError(f"Cannot parse dataset id from {model_path.name}")
    dataset_id = int(match.group(1))
    os.environ["nnUNet_results"] = str(model_path.parent)

    command = [
        _get_nnunet_command_path("nnUNetv2_predict"),
        "-i",
        str(input_images),
        "-o",
        str(output_dir),
        "-d",
        str(dataset_id),
        "-c",
        configuration,
    ]
    if folds is not None:
        if isinstance(folds, list):
            command.extend(["-f"] + [str(_coerce_int("fold", fold)) for fold in folds])
        else:
            command.extend(["-f", str(_coerce_int("fold", folds))])
    if save_probabilities:
        command.append("--save_probabilities")

    _run_subprocess(command, cwd=output_dir, env=_get_nnunet_env(), step_name="predict")
    created = sorted(str(path) for path in output_dir.glob("*.nii*"))
    return {
        "status": "success",
        "output_path": str(output_dir),
        "prediction_files": created,
        "num_predictions": len(created),
    }


def predict(**kwargs: Any) -> dict[str, Any]:
    try:
        if is_model_handle(kwargs.get("0")):
            return _predict_from_handle(**kwargs)
        return _predict_legacy(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet prediction failed: %s", exc)
        raise ValueError(f"nnUNet prediction failed: {exc}") from exc


def train_directory(**kwargs: Any) -> dict[str, Any]:
    """Train nnUNet from pre-existing image and label directories."""
    try:
        for key in ("0", "1", "2", "3"):
            if key not in kwargs:
                raise ValueError(
                    "train_directory requires keys 0..3 (images_dir, labels_dir, modalities, work_dir)"
                )

        images_dir = Path(_coerce_str("images_dir", kwargs["0"]))
        labels_dir = Path(_coerce_str("labels_dir", kwargs["1"]))
        modalities = _normalize_modalities(kwargs["2"])
        work_dir = Path(_coerce_str("work_dir", kwargs["3"]))
        dataset_id = _coerce_int("dataset_id", kwargs.get("4", 1))
        dataset_name = _coerce_str("dataset_name", kwargs.get("5", "VoxLogicADataset"))
        configuration = _coerce_str("configuration", kwargs.get("6", "2d"))
        nfolds = _coerce_int("nfolds", kwargs.get("7", 1))
        device = _coerce_str("device", kwargs.get("8", "cpu")).lower()

        if not images_dir.is_dir():
            raise ValueError(f"images_dir not found: {images_dir}")
        if not labels_dir.is_dir():
            raise ValueError(f"labels_dir not found: {labels_dir}")
        if nfolds <= 0:
            raise ValueError("nfolds must be >= 1")

        materialized = _materialize_directory_dataset(
            images_dir=images_dir,
            labels_dir=labels_dir,
            modalities=modalities,
            work_dir=work_dir,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
        )
        return _run_training_pipeline(
            layout=materialized["layout"],
            dataset_id=dataset_id,
            configuration=configuration,
            nfolds=nfolds,
            device=device,
            labels_sanitized=materialized["labels_sanitized"],
            label_value_map=materialized["label_value_map"],
            dataset_name=dataset_name,
            modalities=modalities,
            manifest_path_value=materialized.get("manifest_path") or str(mat.manifest_path(work_dir)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet train_directory failed: %s", exc)
        raise ValueError(f"nnUNet train_directory failed: {exc}") from exc


def get_primitives() -> Dict[str, Callable[..., Any]]:
    return {
        "train": train,
        "predict": predict,
        "train_directory": train_directory,
        "env_check": env_check,
    }


def list_primitives() -> dict[str, str]:
    return {name: "nnUNet primitive" for name in get_primitives().keys()}


def register_specs() -> Dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    arities = {
        "train": AritySpec(min_args=2, max_args=8),
        "predict": AritySpec(min_args=2, max_args=6),
        "train_directory": AritySpec(min_args=4, max_args=9),
        "env_check": AritySpec.variadic(0),
    }

    descriptions = {
        "train": "Train nnUNet from case sequence or legacy Dask bags",
        "predict": "Run nnUNet inference from model handle or legacy paths",
        "train_directory": "Train nnUNet model from image/label directory layout",
        "env_check": "Inspect nnUNet and torch runtime environment",
    }

    specs: Dict[str, tuple[PrimitiveSpec, Callable[..., Any]]] = {}
    for primitive_name, kernel in get_primitives().items():
        qualified = f"nnunet.{primitive_name}"
        spec = PrimitiveSpec(
            name=primitive_name,
            namespace="nnunet",
            kind="effect",
            arity=arities.get(primitive_name, AritySpec.variadic(0)),
            attrs_schema={},
            planner=default_planner_factory(qualified, kind="effect"),
            kernel_name=qualified,
            description=descriptions.get(primitive_name, "nnUNet primitive"),
        )
        specs[primitive_name] = (spec, kernel)
    return specs


def register_primitives():
    """Legacy compatibility shim."""
    return get_primitives()
