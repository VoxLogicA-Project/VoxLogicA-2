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

from voxlogica.primitives.nnunet.predictor_registry import load as load_predictor
from voxlogica.primitives.nnunet.predictor_registry import reset_runtime_state as reset_predictor_registry
from voxlogica.primitives.nnunet.predictor_registry import store as store_predictor
from voxlogica.primitives.nnunet.cases import DEFAULT_TRAINER, PREDICTOR_KIND, build_model
from voxlogica.primitives.nnunet.io import segmentation_to_sitk, volumes_to_nnunet_array
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
    trainer: str = DEFAULT_TRAINER,
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
    trainer_class = (trainer or DEFAULT_TRAINER).strip()
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
        if trainer_class and trainer_class != DEFAULT_TRAINER:
            train_cmd.extend(["-tr", trainer_class])
        run_cli(train_cmd, cwd=work_root, env=env, step=f"train fold {fold}")
        trained_folds.append(fold)

    resolved_trainer = trainer_dir(results_root, folder, configuration)
    state = load_state(work_root) or {}
    state.update(
        {
            "configuration": configuration,
            "trained_folds": trained_folds,
            "trainer_dir": str(resolved_trainer),
            "trainer": trainer_class,
            "device": device,
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
        device=device,
        trainer=trainer_class,
    )


def _torch_device(device: str) -> Any:
    import torch  # type: ignore

    normalized = str(device or "cpu").lower()
    if normalized in {"cpu", "none", ""}:
        return torch.device("cpu")
    if normalized == "cuda":
        return torch.device("cuda", 0)
    return torch.device(device)


def create_predictor(
    model: dict[str, Any],
    *,
    device: str | None = None,
    folds: list[int] | None = None,
) -> dict[str, Any]:
    """Load an nnU-Net predictor once for repeated image inference."""
    require_nnunet()
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor  # type: ignore

    work_root = Path(model["work_root"])
    _set_nnunet_env(work_root)

    resolved_device = str(device or model.get("device", "cpu")).lower()
    torch_device = _torch_device(resolved_device)
    perform_on_device = torch_device.type == "cuda"

    predictor = nnUNetPredictor(
        tile_step_size=0.5,
        use_gaussian=True,
        use_mirroring=True,
        perform_everything_on_device=perform_on_device,
        device=torch_device,
        verbose=False,
        verbose_preprocessing=False,
        allow_tqdm=False,
    )
    fold_list = tuple(folds if folds is not None else model.get("trained_folds", (0,)))
    predictor.initialize_from_trained_model_folder(
        str(model["trainer_dir"]),
        use_folds=fold_list,
        checkpoint_name="checkpoint_final.pth",
    )
    return {
        "vox_kind": PREDICTOR_KIND,
        "predictor_id": store_predictor(predictor),
        "model": model,
        "device": resolved_device,
        "folds": list(fold_list),
    }


def predict_image(predictor_handle: dict[str, Any], volumes: Any) -> Any:
    """Run nnU-Net inference on one case and return a segmentation image."""
    from voxlogica.primitives.nnunet.cases import normalize_modality_volumes

    predictor_id = str(predictor_handle.get("predictor_id", "")).strip()
    if not predictor_id:
        raise ValueError("predictor handle is missing predictor_id")
    predictor = load_predictor(predictor_id)

    model = predictor_handle["model"]
    modality_volumes = normalize_modality_volumes(
        volumes,
        expected=len(model["modalities"]),
        name="image",
    )
    array, properties = volumes_to_nnunet_array(modality_volumes)
    segmentation = predictor.predict_single_npy_array(array, properties, None, None, False)
    return segmentation_to_sitk(segmentation, properties)


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
