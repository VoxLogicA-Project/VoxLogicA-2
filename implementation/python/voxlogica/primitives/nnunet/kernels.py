"""nnUNet primitives for VoxLogicA-2 (clean implementation)."""

from __future__ import annotations

import os
import sys
import json
import shutil
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List
import re
import subprocess
import importlib.util
from datetime import datetime

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory

logger = logging.getLogger(__name__)

# nnUNet primitives


def _get_nnunet_env():
    """Get environment with proper virtual environment setup for nnUNet commands."""
    env = os.environ.copy()

    # Try to detect virtual environment
    venv_path = None
    project_root = Path(__file__).parent.parent.parent.parent.parent.parent

    if "VIRTUAL_ENV" in env:
        venv_path = Path(env["VIRTUAL_ENV"])
    else:
        # Look for .venv in current directory or project root
        current_dir = Path.cwd()
        for check_dir in [current_dir, project_root]:
            potential_venv = check_dir / ".venv"
            if potential_venv.exists() and (potential_venv / "bin").exists():
                venv_path = potential_venv
                break

    if venv_path and venv_path.exists():
        # Update PATH to include virtual environment bin directory
        venv_bin = str(venv_path / "bin")
        if "PATH" in env:
            env["PATH"] = f"{venv_bin}:{env['PATH']}"
        else:
            env["PATH"] = venv_bin

        # Set VIRTUAL_ENV
        env["VIRTUAL_ENV"] = str(venv_path)

        # Update PYTHONPATH to include implementation/python
        impl_python = str(project_root / "implementation" / "python")
        if "PYTHONPATH" in env:
            env["PYTHONPATH"] = f"{impl_python}:{env['PYTHONPATH']}"
        else:
            env["PYTHONPATH"] = impl_python

    return env


def _get_nnunet_command_path(command_name: str) -> str:
    """Get the full path to an nnUNet command, preferring virtual environment."""
    env = _get_nnunet_env()

    # Check if we have a virtual environment
    if "VIRTUAL_ENV" in env:
        venv_bin = Path(env["VIRTUAL_ENV"]) / "bin" / command_name
        if venv_bin.exists():
            return str(venv_bin)

    # Fallback to system PATH
    system_path = shutil.which(command_name)
    if system_path:
        return system_path

    # Final fallback to command name (let subprocess handle it)
    return command_name


def env_check(**_kwargs):
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
        except Exception as e:  # noqa: BLE001
            out["issues"].append(f"torch: {e}")
        try:
            spec = importlib.util.find_spec("nnunetv2")
            if spec is not None:
                import nnunetv2 as _nn  # type: ignore

                out["nnunetv2_available"] = True
                out["nnunetv2_version"] = getattr(_nn, "__version__", "unknown")
            else:
                out["issues"].append("nnunetv2: not found")
        except Exception as e:  # noqa: BLE001
            out["issues"].append(f"nnunetv2: {e}")
    except Exception as e:  # noqa: BLE001
        out["issues"].append(f"unexpected: {e}")
    out["ready"] = out["torch_available"] and out["nnunetv2_available"]
    return out


def train(**kwargs):
    try:
        for k in ("0", "1", "2", "3"):
            if k not in kwargs:
                raise ValueError(
                    "train requires keys 0..3 (images_bag, labels_bag, modalities, work_dir)"
                )
        images_bag = kwargs["0"]
        labels_bag = kwargs["1"]
        modalities = kwargs["2"]
        work_dir = kwargs["3"]
        # Coerce dataset_id to integer (IMGQL numeric literals may be floats)
        raw_dataset_id = kwargs.get("4", 1)
        try:
            dataset_id = int(float(raw_dataset_id))
        except Exception as _e:  # noqa: BLE001
            raise ValueError(f"dataset_id must be int-like: {raw_dataset_id!r}: {_e}")
        dataset_name = kwargs.get("5", "VoxLogicADataset")
        configuration = kwargs.get("6", "3d_fullres")
        nfolds = kwargs.get("7", 5)
        wrapper_base = (
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "doc"
            / "dev"
            / "notes"
        )
        sys.path.insert(0, str(wrapper_base))
        try:
            wrapper_path = wrapper_base / "nnunet_wrapper.py"
            if not wrapper_path.exists():
                raise ImportError("nnunet_wrapper.py missing")
            spec = importlib.util.spec_from_file_location(
                "nnunet_wrapper", str(wrapper_path)
            )
            if spec is None or spec.loader is None:
                raise ImportError("loader error")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore
            nnUNetDaskWrapper = getattr(module, "nnUNetDaskWrapper")
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"nnUNet wrapper not available: {e}")
        wrapper = nnUNetDaskWrapper(
            work_dir=work_dir, dataset_id=dataset_id, dataset_name=dataset_name
        )
        status = wrapper.get_training_status()
        results = wrapper.train(
            images_bag=images_bag,
            labels_bag=labels_bag,
            modalities=modalities,
            configuration=configuration,
            nfolds=nfolds,
            resume=True,
        )
        model_path = wrapper.nnunet_results / wrapper.dataset_full_name
        return {
            "status": "success",
            "model_path": str(model_path),
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "configuration": configuration,
            "nfolds": nfolds,
            "work_dir": work_dir,
            "training_results": results,
            "trained_folds": status.get("trained_folds", []),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"nnUNet training failed: {e}")
        raise ValueError(f"nnUNet training failed: {e}") from e


def predict(**kwargs):
    try:
        for k in ("0", "1", "2"):
            if k not in kwargs:
                raise ValueError(
                    "predict requires keys 0..2 (input_images, model_path, output_dir)"
                )
        input_images = Path(str(kwargs["0"]))
        model_path = Path(str(kwargs["1"]))
        output_dir = Path(str(kwargs["2"]))
        configuration = str(kwargs.get("3", "3d_fullres"))
        folds = kwargs.get("4")
        save_probabilities = bool(kwargs.get("5", False))
        if importlib.util.find_spec("nnunetv2") is None:
            raise ValueError("nnunetv2 not installed")
        if not model_path.exists():
            raise ValueError(f"model path not found: {model_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        m = re.search(r"Dataset(\d{1,3})_", model_path.name)
        if not m:
            raise ValueError(f"Cannot parse dataset id from {model_path.name}")
        dataset_id = int(m.group(1))
        os.environ["nnUNet_results"] = str(model_path.parent)
        cmd = [
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
                cmd.extend(["-f"] + [str(int(f)) for f in folds])
            else:
                cmd.extend(["-f", str(int(folds))])
        if save_probabilities:
            cmd.append("--save_probabilities")
        logger.info("Running (predict): " + " ".join(cmd))
        pr = subprocess.run(cmd, capture_output=True, text=True, env=_get_nnunet_env())
        if pr.returncode != 0:
            raise ValueError(f"nnUNet predict failed: {pr.stderr}")
        created = sorted(str(p) for p in output_dir.glob("*.nii*"))
        return {
            "status": "success",
            "output_path": str(output_dir),
            "prediction_files": created,
            "num_predictions": len(created),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"nnUNet prediction failed: {e}")
        raise ValueError(f"nnUNet prediction failed: {e}") from e


def train_directory(**kwargs):
    try:
        # Entry point for train_directory
        for k in ("0", "1", "2", "3"):
            if k not in kwargs:
                raise ValueError(
                    "train_directory requires keys 0..3 (images_dir, labels_dir, modalities, work_dir)"
                )
        images_dir = Path(str(kwargs["0"]))
        labels_dir = Path(str(kwargs["1"]))
        modalities_arg = kwargs["2"]
        work_dir = Path(str(kwargs["3"]))
        # Coerce dataset_id to integer early (IMGQL numeric literals may be floats)
        raw_dataset_id = kwargs.get("4", 1)
        try:
            dataset_id = int(float(raw_dataset_id))
        except Exception as _e:  # noqa: BLE001
            raise ValueError(f"dataset_id must be int-like: {raw_dataset_id!r}: {_e}")
        dataset_name = str(kwargs.get("5", "VoxLogicADataset"))
        configuration = str(kwargs.get("6", "2d"))
        nfolds = int(kwargs.get("7", 1))
        device = str(kwargs.get("8", "cpu")).lower()
        if not images_dir.is_dir():
            raise ValueError(f"images_dir not found: {images_dir}")
        if not labels_dir.is_dir():
            raise ValueError(f"labels_dir not found: {labels_dir}")
        if importlib.util.find_spec("nnunetv2") is None:
            raise ValueError("nnunetv2 not installed")
        nnunet_raw = work_dir / "nnUNet_raw"
        nnunet_preprocessed = work_dir / "nnUNet_preprocessed"
        nnunet_results = work_dir / "nnUNet_results"
        for d in (nnunet_raw, nnunet_preprocessed, nnunet_results):
            d.mkdir(parents=True, exist_ok=True)
        os.environ["nnUNet_raw"] = str(nnunet_raw)
        os.environ["nnUNet_preprocessed"] = str(nnunet_preprocessed)
        os.environ["nnUNet_results"] = str(nnunet_results)
        logger.info(f"[train_directory] dataset_id coerced type={type(dataset_id).__name__} value={dataset_id!r}")
        try:
            int(dataset_id)
        except Exception as _e:  # noqa: BLE001
            raise ValueError(
                f"dataset_id not convertible to int: {dataset_id!r} ({type(dataset_id).__name__})"
            )
        try:
            padded_id = str(int(dataset_id)).zfill(3)
        except Exception as _e:  # noqa: BLE001
            raise ValueError(f"Failed to pad dataset_id {dataset_id!r}: {_e}")

        # Detect conflicting dataset names for the same dataset id (common when reusing id=1 repeatedly)
        conflict_dirs = sorted(
            {p.name for p in (work_dir / "nnUNet_raw").glob(f"Dataset{padded_id}_*") if p.is_dir()}
        )
        if len(conflict_dirs) > 1 and f"Dataset{padded_id}_{dataset_name}" not in conflict_dirs:
            raise ValueError(
                (
                    "Multiple dataset names already exist for dataset id "
                    f"{dataset_id} (found: {conflict_dirs}). Either:\n"
                    f" - remove conflicting directories under {work_dir}/nnUNet_raw/nnUNet_preprocessed/nnUNet_results\n"
                    " - choose a different dataset id"
                )
            )

        padded_name = f"Dataset{padded_id}_{dataset_name}"
        unpadded_name = f"Dataset{dataset_id}_{dataset_name}"
        dataset_dir = nnunet_raw / padded_name
        imagesTr = dataset_dir / "imagesTr"
        labelsTr = dataset_dir / "labelsTr"
        imagesTs = dataset_dir / "imagesTs"
        for d in (imagesTr, labelsTr, imagesTs):
            d.mkdir(parents=True, exist_ok=True)
        label_files = sorted(p for p in labels_dir.glob("*.nii*") if p.is_file())
        if not label_files:
            raise ValueError(f"No label files in {labels_dir}")
        # Inspect and sanitize labels (nnUNet integrity expects consecutive integers starting at 0)
        # We currently assume binary foreground/background for synthetic data; collapse any non-zero to 1.
        import SimpleITK as sitk  # type: ignore

        label_value_map: Dict[str, List[int]] = {}
        sanitized_dir = work_dir / "sanitized_labels"
        sanitized_dir.mkdir(parents=True, exist_ok=True)
        sanitized_used = False
        for lbl in label_files:
            try:
                img = sitk.ReadImage(str(lbl))
                arr = sitk.GetArrayFromImage(img)
                unique_vals = sorted({int(x) for x in set(arr.flatten())})  # type: ignore[arg-type]
                label_value_map[lbl.name] = unique_vals
                # If any value not in (0,1) then sanitize by mapping >0 -> 1
                if any(v not in (0, 1) for v in unique_vals):
                    sanitized_used = True
                    if arr.dtype.kind in ("f", "d"):
                        # numerical stability: threshold at >0.5
                        import numpy as _np  # type: ignore

                        arr_bin = (_np.asarray(arr) > 0.5).astype("uint8")
                    else:
                        import numpy as _np  # type: ignore

                        arr_bin = (_np.asarray(arr) > 0).astype("uint8")
                    out_img = sitk.GetImageFromArray(arr_bin)
                    out_img.CopyInformation(img)
                    out_path = sanitized_dir / lbl.name
                    sitk.WriteImage(out_img, str(out_path))
            except Exception as _le:  # noqa: BLE001
                logger.warning(f"Label inspection failed for {lbl}: {_le}")
        if sanitized_used:
            logger.info(
                "[train_directory] Sanitizing labels: collapsing unexpected label values to binary {0,1}"
            )
            # Replace label_files with sanitized versions where created
            new_label_files = []
            for lbl in label_files:
                sanitized_path = sanitized_dir / lbl.name
                new_label_files.append(
                    sanitized_path if sanitized_path.exists() else lbl
                )
            label_files = new_label_files  # type: ignore
        if isinstance(modalities_arg, str):
            modalities: List[str] = [modalities_arg]
        elif isinstance(modalities_arg, list):
            modalities = [str(m) for m in modalities_arg]
        else:
            raise ValueError("modalities must be string or list")

        def _link(src: Path, dst: Path):
            try:
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                os.symlink(src, dst)
            except Exception:  # noqa: BLE001
                shutil.copy2(src, dst)

        for lbl in label_files:
            case_name = lbl.name.split(".")[0]
            for mod_idx, _m in enumerate(modalities):
                try:
                    midx = int(mod_idx)
                except Exception:
                    logger.error(
                        f"Non-integer modality index encountered: {mod_idx!r} (type={type(mod_idx).__name__}); coercing via int(float(.)) if possible"
                    )
                    try:
                        midx = int(float(mod_idx))  # type: ignore[arg-type]
                    except Exception as ce:  # noqa: BLE001
                        raise ValueError(
                            f"Cannot coerce modality index {mod_idx!r} to int: {ce}"
                        )
                midx_str = str(midx).split(".")[0]  # strip any decimal part defensively
                midx_padded = midx_str.zfill(4)
                matches = list(images_dir.glob(f"{case_name}_{midx_padded}*"))
                if not matches:
                    logger.warning(f"Missing modality {midx_padded} for {case_name}")
                    continue
                img_src = matches[0]
                suffix = "".join(img_src.suffixes) or ".nii.gz"
                _link(img_src, imagesTr / f"{case_name}_{midx_padded}{suffix}")
            _link(lbl, labelsTr / "".join([case_name] + list(lbl.suffixes)))
        dataset_json = {
            "channel_names": {str(i): m for i, m in enumerate(modalities)},
            "labels": {"background": 0, "label_1": 1},
            "numTraining": len(label_files),
            "file_ending": ".nii.gz",
            "dataset_name": dataset_name,
        }
        (dataset_dir / "dataset.json").write_text(json.dumps(dataset_json, indent=2))

        # Setup environment for nnUNet commands
        base_env = _get_nnunet_env()
        # Force CPU if requested (avoid CUDA assertion on systems without GPU)
        if False and device in ("cpu", "none"):  # TODO: this was broken
            base_env["CUDA_VISIBLE_DEVICES"] = ""

        plan_cmd = [
            _get_nnunet_command_path("nnUNetv2_plan_and_preprocess"),
            "-d",
            str(int(dataset_id)),
            "--verify_dataset_integrity",
            "-c",
            configuration,
        ]
        logger.info("Running (plan): " + " ".join(plan_cmd))
        pp = subprocess.run(
            plan_cmd, cwd=str(work_dir), capture_output=True, text=True, env=base_env
        )
        if pp.returncode != 0:
            raise ValueError(f"nnUNet preprocessing failed: {pp.stderr}")
        fold_results: List[Dict[str, Any]] = []
        start = datetime.now()
        # base_env already configured above
        for fold in range(nfolds):
            # nnUNetv2_train does not expose a --device flag; CPU enforcement is via CUDA_VISIBLE_DEVICES.
            train_cmd = [
                _get_nnunet_command_path("nnUNetv2_train"),
                str(int(dataset_id)),
                configuration,
                str(fold),
            ]
            logger.info("Running (train): " + " ".join(train_cmd))
            tr = subprocess.run(
                train_cmd,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                env=base_env,
            )
            fold_results.append(
                {
                    "fold": fold,
                    "status": "success" if tr.returncode == 0 else "failed",
                    "stdout": tr.stdout[-2000:],
                    "stderr": tr.stderr[-2000:],
                }
            )
            if tr.returncode != 0:
                raise ValueError(f"nnUNet training fold {fold} failed: {tr.stderr}")
        training_time = (datetime.now() - start).total_seconds()
        # Compatibility symlink for unpadded name inside results
        padded_results_dir = nnunet_results / padded_name
        unpadded_results_dir = nnunet_results / unpadded_name
        if padded_results_dir.exists() and not unpadded_results_dir.exists():
            try:
                os.symlink(padded_results_dir, unpadded_results_dir)
            except Exception:  # noqa: BLE001
                pass
        return {
            "status": "success",
            "model_path": str(
                unpadded_results_dir
                if unpadded_results_dir.exists()
                else padded_results_dir
            ),
            "dataset_id": dataset_id,
            "configuration": configuration,
            "fold_results": fold_results,
            "training_time": training_time,
            "final_metrics": {},
            "device": device,
            "labels_sanitized": sanitized_used,
            "label_value_map": label_value_map,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"nnUNet train_directory failed: {e}")
        raise ValueError(f"nnUNet train_directory failed: {e}") from e


def get_primitives() -> Dict[str, Callable[..., Any]]:
    return {
        "train": train,
        "predict": predict,
        "train_directory": train_directory,
        "env_check": env_check,
    }


def list_primitives():
    return {k: "nnUNet primitive" for k in get_primitives().keys()}


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
