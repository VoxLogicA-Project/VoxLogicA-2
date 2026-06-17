"""nnUNet CLI orchestration."""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from voxlogica.primitives.nnunet.manifest import load_manifest, save_manifest
from voxlogica.primitives.nnunet.materialize import prepare_runtime_roots
from voxlogica.primitives.nnunet.types import build_model_handle

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[5]


def get_nnunet_env() -> dict[str, str]:
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


def get_nnunet_command_path(command_name: str) -> str:
    env = get_nnunet_env()
    if "VIRTUAL_ENV" in env:
        candidate = Path(env["VIRTUAL_ENV"]) / "bin" / command_name
        if candidate.exists():
            return str(candidate)
    return shutil.which(command_name) or command_name


def require_nnunet_installation() -> None:
    if importlib.util.find_spec("nnunetv2") is None:
        raise ValueError("nnunetv2 not installed")


def run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    step_name: str,
) -> subprocess.CompletedProcess[str]:
    logger.info("Running (%s): %s", step_name, " ".join(command))
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, env=env)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ValueError(f"{step_name} failed: {stderr or 'unknown error'}")
    return result


def resolve_trainer_dir(nnunet_results: Path, dataset_folder: str, configuration: str) -> Path:
    dataset_results = nnunet_results / dataset_folder
    if not dataset_results.is_dir():
        raise ValueError(f"nnUNet results folder not found: {dataset_results}")
    exact = dataset_results / f"nnUNetTrainer__nnUNetPlans__{configuration}"
    if exact.is_dir():
        return exact
    matches = sorted(path for path in dataset_results.glob("nnUNetTrainer__*") if path.is_dir())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no trainer directory under {dataset_results}")
    for candidate in matches:
        if configuration in candidate.name:
            return candidate
    raise ValueError(f"ambiguous trainer directories under {dataset_results}: {[p.name for p in matches]}")


def fold_is_complete(trainer_dir: Path, fold: int) -> bool:
    checkpoint = trainer_dir / f"fold_{fold}" / "checkpoint_final.pth"
    return checkpoint.is_file()


def run_training_pipeline(
    *,
    layout: dict[str, Any],
    dataset_id: int,
    dataset_name: str,
    configuration: str,
    nfolds: int,
    device: str,
    labels: dict[str, int],
    labels_sanitized: bool,
    label_value_map: dict[str, list[int]],
    manifest_path_value: str,
    modalities: list[str],
    continue_training: bool = True,
    verify_integrity: bool = True,
) -> dict[str, Any]:
    require_nnunet_installation()
    work_root = Path(layout["work_dir"])
    prepare_runtime_roots(work_root)
    base_env = get_nnunet_env()
    if device in {"cpu", "none"}:
        base_env["CUDA_VISIBLE_DEVICES"] = ""

    plan_cmd = [
        get_nnunet_command_path("nnUNetv2_plan_and_preprocess"),
        "-d",
        str(dataset_id),
        "-c",
        configuration,
    ]
    if verify_integrity:
        plan_cmd.append("--verify_dataset_integrity")
    run_subprocess(plan_cmd, cwd=work_root, env=base_env, step_name="plan")

    trainer_dir: Path | None = None
    try:
        trainer_dir = resolve_trainer_dir(layout["nnunet_results"], layout["padded_name"], configuration)
    except ValueError:
        pass

    fold_results: list[dict[str, Any]] = []
    trained_folds: list[int] = []
    start = datetime.now()

    for fold in range(nfolds):
        if continue_training and trainer_dir is not None and fold_is_complete(trainer_dir, fold):
            fold_results.append({"fold": fold, "status": "skipped", "reason": "checkpoint exists"})
            trained_folds.append(fold)
            continue
        train_cmd = [
            get_nnunet_command_path("nnUNetv2_train"),
            str(dataset_id),
            configuration,
            str(fold),
        ]
        result = run_subprocess(train_cmd, cwd=work_root, env=base_env, step_name=f"train fold {fold}")
        fold_results.append(
            {
                "fold": fold,
                "status": "success",
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
        )
        trained_folds.append(fold)

    trainer_dir = resolve_trainer_dir(layout["nnunet_results"], layout["padded_name"], configuration)
    manifest = load_manifest(work_root) or {}
    manifest.update(
        {
            "configuration": configuration,
            "trained_folds": trained_folds,
            "trainer_dir": str(trainer_dir),
        }
    )
    save_manifest(work_root, manifest)

    handle = build_model_handle(
        work_root=str(work_root),
        dataset_id=dataset_id,
        dataset_folder=str(layout["padded_name"]),
        dataset_name=dataset_name,
        configuration=configuration,
        modalities=modalities,
        nfolds=nfolds,
        trained_folds=trained_folds,
        trainer_dir=str(trainer_dir),
        labels=labels,
        manifest_path=manifest_path_value,
    )
    handle.update(
        {
            "fold_results": fold_results,
            "training_time": (datetime.now() - start).total_seconds(),
            "labels_sanitized": labels_sanitized,
            "label_value_map": label_value_map,
            "training_results": {
                "fold_results": fold_results,
                "training_time": (datetime.now() - start).total_seconds(),
            },
        }
    )
    return handle


def run_prediction_pipeline(
    *,
    model_handle: dict[str, Any],
    input_dir: Path,
    output_dir: Path,
    folds: list[int] | None = None,
    save_probabilities: bool = False,
) -> dict[str, Any]:
    require_nnunet_installation()
    work_root = Path(model_handle["work_root"])
    prepare_runtime_roots(work_root)
    env = get_nnunet_env()
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        get_nnunet_command_path("nnUNetv2_predict"),
        "-i",
        str(input_dir),
        "-o",
        str(output_dir),
        "-d",
        str(int(model_handle["dataset_id"])),
        "-c",
        str(model_handle["configuration"]),
    ]
    if folds is not None:
        command.extend(["-f"] + [str(fold) for fold in folds])
    if save_probabilities:
        command.append("--save_probabilities")

    run_subprocess(command, cwd=output_dir, env=env, step_name="predict")
    created = sorted(str(path) for path in output_dir.glob("*.nii*"))
    case_results = []
    for path_str in created:
        path = Path(path_str)
        case_results.append(
            {
                "case_id": path.name.split(".")[0],
                "segmentation_path": path_str,
            }
        )
    return {
        "status": "success",
        "model": model_handle,
        "output_path": str(output_dir),
        "prediction_files": created,
        "num_predictions": len(created),
        "cases": case_results,
    }
