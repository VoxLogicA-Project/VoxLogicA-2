"""nnUNet primitives for VoxLogicA-2."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_NNUNET_FILE_ENDING = ".nii.gz"


def _get_nnunet_env() -> dict[str, str]:
    """Build an environment that prefers the project's virtualenv when present."""
    env = os.environ.copy()
    venv_path: Path | None = None

    if "VIRTUAL_ENV" in env:
        venv_path = Path(env["VIRTUAL_ENV"])
    else:
        for check_dir in (Path.cwd(), _PROJECT_ROOT):
            candidate = check_dir / ".venv"
            if (candidate / "bin").exists():
                venv_path = candidate
                break

    if venv_path and venv_path.exists():
        venv_bin = str(venv_path / "bin")
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}".rstrip(":")
        env["VIRTUAL_ENV"] = str(venv_path)

        impl_python = str(_PROJECT_ROOT / "implementation" / "python")
        env["PYTHONPATH"] = f"{impl_python}:{env.get('PYTHONPATH', '')}".rstrip(":")

    return env


def _get_nnunet_command_path(command_name: str) -> str:
    """Resolve an nnUNet command, preferring the active virtualenv."""
    env = _get_nnunet_env()

    if "VIRTUAL_ENV" in env:
        candidate = Path(env["VIRTUAL_ENV"]) / "bin" / command_name
        if candidate.exists():
            return str(candidate)

    system_path = shutil.which(command_name)
    return system_path or command_name


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


def _normalize_modalities(value: Any) -> List[str]:
    if isinstance(value, str):
        modalities = [value]
    elif isinstance(value, (list, tuple)):
        modalities = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise ValueError("modalities must be a string or list")

    if not modalities:
        raise ValueError("modalities cannot be empty")

    return modalities


def _require_nnunet_installation() -> None:
    if importlib.util.find_spec("nnunetv2") is None:
        raise ValueError("nnunetv2 not installed")


def _prepare_runtime_roots(work_dir: Path) -> dict[str, Any]:
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


def _dataset_names(dataset_id: int, dataset_name: str) -> dict[str, str]:
    padded_id = str(dataset_id).zfill(3)
    return {
        "padded_id": padded_id,
        "padded_name": f"Dataset{padded_id}_{dataset_name}",
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
    runtime = _prepare_runtime_roots(work_dir)
    _ensure_dataset_name_consistency(runtime["nnunet_raw"], dataset_id, dataset_name)
    names = _dataset_names(dataset_id, dataset_name)

    dataset_dir = runtime["nnunet_raw"] / names["padded_name"]
    images_tr = dataset_dir / "imagesTr"
    labels_tr = dataset_dir / "labelsTr"
    images_ts = dataset_dir / "imagesTs"
    for directory in (images_tr, labels_tr, images_ts):
        directory.mkdir(parents=True, exist_ok=True)

    return {
        **runtime,
        **names,
        "dataset_dir": dataset_dir,
        "imagesTr": images_tr,
        "labelsTr": labels_tr,
        "imagesTs": images_ts,
    }


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src, dst)
    except Exception:  # noqa: BLE001
        shutil.copy2(src, dst)


def _sanitize_case_name(case_id: Any) -> str:
    text = str(case_id).strip()
    if not text:
        raise ValueError("case_id cannot be empty")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)


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
    np, nib = _load_array_io_modules()
    image = nib.Nifti1Image(np.asarray(array), np.eye(4))
    nib.save(image, str(destination))


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
    }


def _write_dataset_json(
    dataset_dir: Path,
    modalities: list[str],
    dataset_name: str,
    num_training: int,
) -> None:
    dataset_json = {
        "channel_names": {str(index): modality for index, modality in enumerate(modalities)},
        "labels": {"background": 0, "label_1": 1},
        "numTraining": num_training,
        "file_ending": _NNUNET_FILE_ENDING,
        "dataset_name": dataset_name,
    }
    (dataset_dir / "dataset.json").write_text(
        json.dumps(dataset_json, indent=2),
        encoding="utf-8",
    )


def _run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    step_name: str,
) -> subprocess.CompletedProcess[str]:
    logger.info("Running (%s): %s", step_name, " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ValueError(f"{step_name} failed: {stderr or 'unknown error'}")
    return result


def _run_training_pipeline(
    *,
    layout: dict[str, Any],
    dataset_id: int,
    configuration: str,
    nfolds: int,
    device: str,
    labels_sanitized: bool,
    label_value_map: dict[str, list[int]],
) -> dict[str, Any]:
    _require_nnunet_installation()
    base_env = _get_nnunet_env()

    if device in {"cpu", "none"}:
        base_env["CUDA_VISIBLE_DEVICES"] = ""

    plan_cmd = [
        _get_nnunet_command_path("nnUNetv2_plan_and_preprocess"),
        "-d",
        str(dataset_id),
        "--verify_dataset_integrity",
        "-c",
        configuration,
    ]
    _run_subprocess(plan_cmd, cwd=layout["work_dir"], env=base_env, step_name="plan")

    fold_results: list[dict[str, Any]] = []
    start = datetime.now()
    for fold in range(nfolds):
        train_cmd = [
            _get_nnunet_command_path("nnUNetv2_train"),
            str(dataset_id),
            configuration,
            str(fold),
        ]
        result = _run_subprocess(
            train_cmd,
            cwd=layout["work_dir"],
            env=base_env,
            step_name=f"train fold {fold}",
        )
        fold_results.append(
            {
                "fold": fold,
                "status": "success",
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
        )

    padded_results_dir = layout["nnunet_results"] / layout["padded_name"]
    unpadded_results_dir = layout["nnunet_results"] / layout["unpadded_name"]
    if padded_results_dir.exists() and not unpadded_results_dir.exists():
        try:
            os.symlink(padded_results_dir, unpadded_results_dir)
        except Exception:  # noqa: BLE001
            pass

    return {
        "status": "success",
        "model_path": str(
            unpadded_results_dir if unpadded_results_dir.exists() else padded_results_dir
        ),
        "dataset_id": dataset_id,
        "dataset_name": layout["padded_name"].split("_", 1)[1],
        "configuration": configuration,
        "nfolds": nfolds,
        "work_dir": str(layout["work_dir"]),
        "fold_results": fold_results,
        "training_time": (datetime.now() - start).total_seconds(),
        "final_metrics": {},
        "device": device,
        "labels_sanitized": labels_sanitized,
        "label_value_map": label_value_map,
    }


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
    """Train nnUNet from bag-like image and label collections."""
    try:
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
        )
        result["training_results"] = {
            "fold_results": result["fold_results"],
            "training_time": result["training_time"],
        }
        result["trained_folds"] = [item["fold"] for item in result["fold_results"]]
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet training failed: %s", exc)
        raise ValueError(f"nnUNet training failed: {exc}") from exc


def predict(**kwargs: Any) -> dict[str, Any]:
    try:
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
        "train": AritySpec(min_args=4, max_args=8),
        "predict": AritySpec(min_args=3, max_args=6),
        "train_directory": AritySpec(min_args=4, max_args=9),
        "env_check": AritySpec.variadic(0),
    }

    descriptions = {
        "train": "Train nnUNet model from bag-based dataset inputs",
        "predict": "Run nnUNet inference from trained model",
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
