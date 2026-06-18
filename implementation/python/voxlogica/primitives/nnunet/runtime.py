"""nnUNet CLI orchestration."""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from voxlogica.primitives.nnunet.cases import build_model
from voxlogica.primitives.nnunet.materialize import _set_nnunet_env, load_state, save_state

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[5]


def nnunet_env() -> dict[str, str]:
    env = os.environ.copy()
    venv = env.get("VIRTUAL_ENV")
    if not venv:
        for check_dir in (Path.cwd(), _PROJECT_ROOT):
            candidate = check_dir / ".venv"
            if (candidate / "bin").exists():
                venv = str(candidate)
                break
    if venv:
        env["PATH"] = f"{Path(venv) / 'bin'}:{env.get('PATH', '')}".rstrip(":")
        env["VIRTUAL_ENV"] = venv
        impl_python = str(_PROJECT_ROOT / "implementation" / "python")
        env["PYTHONPATH"] = f"{impl_python}:{env.get('PYTHONPATH', '')}".rstrip(":")
    return env


def nnunet_command(name: str) -> str:
    env = nnunet_env()
    if "VIRTUAL_ENV" in env:
        candidate = Path(env["VIRTUAL_ENV"]) / "bin" / name
        if candidate.exists():
            return str(candidate)
    return shutil.which(name) or name


def require_nnunet() -> None:
    if importlib.util.find_spec("nnunetv2") is None:
        raise ValueError("nnunetv2 not installed")


def run_cli(command: list[str], *, cwd: Path, env: dict[str, str], step: str) -> None:
    logger.info("Running (%s): %s", step, " ".join(command))
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise ValueError(f"{step} failed: {(result.stderr or '').strip() or 'unknown error'}")


def trainer_dir(nnunet_results: Path, dataset_folder: str, configuration: str) -> Path:
    dataset_results = nnunet_results / dataset_folder
    if not dataset_results.is_dir():
        raise ValueError(f"nnUNet results folder not found: {dataset_results}")
    suffix = f"__nnUNetPlans__{configuration}"
    matches = sorted(
        path for path in dataset_results.iterdir() if path.is_dir() and path.name.endswith(suffix)
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no trainer directory under {dataset_results}")
    raise ValueError(f"ambiguous trainer directories: {[path.name for path in matches]}")


def trainer_name(trainer_path: str | Path) -> str:
    name = Path(trainer_path).name
    marker = "__nnUNetPlans__"
    return name.split(marker, 1)[0] if marker in name else "nnUNetTrainer"


def fold_complete(trainer_path: Path, fold: int) -> bool:
    return (trainer_path / f"fold_{fold}" / "checkpoint_final.pth").is_file()


def train_model(
    *,
    layout: dict[str, Any],
    dataset_id: int,
    dataset_name: str,
    configuration: str,
    modalities: list[str],
    nfolds: int,
    device: str,
    labels: dict[str, int],
) -> dict[str, Any]:
    require_nnunet()
    work_root = Path(layout["work_dir"])
    _set_nnunet_env(work_root)
    env = nnunet_env()
    if device in {"cpu", "none"}:
        env["CUDA_VISIBLE_DEVICES"] = ""

    plan_cmd = [
        nnunet_command("nnUNetv2_plan_and_preprocess"),
        "-d",
        str(dataset_id),
        "-c",
        configuration,
        "--verify_dataset_integrity",
    ]
    run_cli(plan_cmd, cwd=work_root, env=env, step="plan")

    results_root = Path(layout["nnunet_results"])
    folder = str(layout["dataset_folder"])
    current_trainer: Path | None = None
    try:
        current_trainer = trainer_dir(results_root, folder, configuration)
    except ValueError:
        pass

    trained_folds: list[int] = []
    custom_trainer = os.environ.get("VOXLOGICA_NNUNET_TRAINER", "").strip()
    train_device = "cpu" if device in {"cpu", "none"} else "cuda"

    for fold in range(nfolds):
        if current_trainer is not None and fold_complete(current_trainer, fold):
            trained_folds.append(fold)
            continue
        train_cmd = [
            nnunet_command("nnUNetv2_train"),
            str(dataset_id),
            configuration,
            str(fold),
            "-device",
            train_device,
        ]
        if custom_trainer:
            train_cmd.extend(["-tr", custom_trainer])
        run_cli(train_cmd, cwd=work_root, env=env, step=f"train fold {fold}")
        trained_folds.append(fold)

    resolved_trainer = trainer_dir(results_root, folder, configuration)
    state = load_state(work_root) or {}
    state.update(
        {
            "configuration": configuration,
            "trained_folds": trained_folds,
            "trainer_dir": str(resolved_trainer),
        }
    )
    save_state(work_root, state)

    return build_model(
        work_root=str(work_root),
        dataset_id=dataset_id,
        dataset_folder=folder,
        configuration=configuration,
        modalities=modalities,
        trained_folds=trained_folds,
        trainer_dir=str(resolved_trainer),
        labels=labels,
    )


def predict_cases(
    *,
    model: dict[str, Any],
    input_dir: Path,
    output_dir: Path,
    folds: list[int] | None = None,
    save_probabilities: bool = False,
) -> dict[str, Any]:
    require_nnunet()
    work_root = Path(model["work_root"])
    _set_nnunet_env(work_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        nnunet_command("nnUNetv2_predict"),
        "-i",
        str(input_dir),
        "-o",
        str(output_dir),
        "-d",
        str(int(model["dataset_id"])),
        "-tr",
        trainer_name(model["trainer_dir"]),
        "-c",
        str(model["configuration"]),
    ]
    fold_list = folds if folds is not None else model.get("trained_folds")
    if fold_list:
        command.extend(["-f"] + [str(fold) for fold in fold_list])
    if save_probabilities:
        command.append("--save_probabilities")

    run_cli(command, cwd=output_dir, env=nnunet_env(), step="predict")
    created = sorted(output_dir.glob("*.nii*"))
    return {
        "status": "success",
        "model": model,
        "output_path": str(output_dir),
        "prediction_files": [str(path) for path in created],
        "num_predictions": len(created),
        "cases": [
            {"case_id": path.name.split(".")[0], "segmentation_path": str(path)} for path in created
        ],
    }


def export_prediction_pngs(predictions: dict[str, Any], export_root: str | Path) -> list[str]:
    """Write PNG previews of nnUNet segmentations for gallery and inspection."""
    try:
        import SimpleITK as sitk  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"export_predictions requires SimpleITK: {exc}") from exc

    root = Path(export_root) / "predictions"
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for case in predictions.get("cases", []):
        case_id = str(case["case_id"])
        nii_path = Path(str(case["segmentation_path"]))
        image = sitk.ReadImage(str(nii_path))
        array = sitk.GetArrayFromImage(image)
        png_image = sitk.GetImageFromArray(array.astype("float32"))
        png_image = sitk.Cast(sitk.RescaleIntensity(png_image, 0, 255), sitk.sitkUInt8)
        out_path = root / f"{case_id}_segmentation.png"
        sitk.WriteImage(png_image, str(out_path))
        written.append(str(out_path))
    return written


def env_check() -> dict[str, Any]:
    out: dict[str, Any] = {
        "torch_available": False,
        "torch_version": None,
        "nnunetv2_available": False,
        "nnunetv2_version": None,
        "issues": [],
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    try:
        import torch  # type: ignore

        out["torch_available"] = True
        out["torch_version"] = getattr(torch, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        out["issues"].append(f"torch: {exc}")
    try:
        if importlib.util.find_spec("nnunetv2") is None:
            out["issues"].append("nnunetv2: not found")
        else:
            import nnunetv2 as nnunet_module  # type: ignore

            out["nnunetv2_available"] = True
            out["nnunetv2_version"] = getattr(nnunet_module, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        out["issues"].append(f"nnunetv2: {exc}")
    out["ready"] = out["torch_available"] and out["nnunetv2_available"]
    return out
