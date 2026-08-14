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
from voxlogica.primitives.nnunet.predictor_registry import has as predictor_registered
from voxlogica.primitives.nnunet.predictor_registry import lock_for as predictor_lock
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
    # nnU-Net compiles the network with torch.compile by default, and on this
    # stack (torch 2.10 + cu128, RTX PRO 5000 Blackwell, sm_120) the compiled
    # graph silently does not train: 250 epochs on 30 BraTS FLAIR cases ended at
    # train_loss -0.17, pseudo dice 0.0, and ZERO predicted foreground voxels on
    # every validation case. The identical dataset, plans and preprocessing with
    # compilation off reached validation Dice 0.854 in TEN epochs. Nothing about
    # the data or the configuration differed -- only this switch.
    #
    # Defaulting it off costs about 20% of epoch time (27 s -> 35 s here) and
    # buys a model that learns. Set nnUNet_compile explicitly to opt back in
    # once the stack is known good.
    env.setdefault("nnUNet_compile", "f")
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


def _emit(line: str) -> None:
    """Put one child line on the engine's own output, above the progress bar.

    Training runs for minutes inside a single node, so its output is the only
    sign of life there is; losing it means watching a bar that cannot move. A
    plain write races the bar and gets overwritten on the next refresh, which
    is why this goes through ``tqdm.write`` when a bar exists -- that is the
    documented way to print without corrupting it. Without tqdm installed, or
    with no bar active, it is an ordinary write.
    """
    text = f"[nnunet] {line.rstrip()}"
    try:
        from tqdm import tqdm

        tqdm.write(text, file=sys.stdout)
        return
    except Exception:  # noqa: BLE001 -- output must never break a training run
        pass
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def run_cli(command: list[str], *, cwd: Path, env: dict[str, str], step: str) -> None:
    _emit(f"{step}: {' '.join(command)}")
    logger.info("Starting %s: %s", step, " ".join(command))
    child_env = dict(env)
    child_env.setdefault("PYTHONUNBUFFERED", "1")

    # ALWAYS capture and forward, never inherit the terminal. Inheriting was
    # the isatty branch, and under a pty (how long runs are launched) it let
    # the child write straight into the same terminal the progress bar owns:
    # the two interleave and the bar erases whatever landed on its line.
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    captured: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        captured.append(line)
        _emit(line)
    returncode = process.wait()
    if returncode != 0:
        tail = "".join(captured[-80:]).strip()
        raise ValueError(f"{step} failed with exit code {returncode}:\n{tail or 'unknown error'}")
    _emit(f"{step}: done")
    logger.info("Completed %s", step)


def trainer_dir(nnunet_results: Path, dataset_folder: str, configuration: str,
                trainer: str | None = None) -> Path:
    """The results directory of THIS trainer, not of whichever one is there.

    Resolving by shape instead of by identity was wrong in both directions. Two
    trainers in one dataset folder made it raise "ambiguous trainer
    directories" and refuse to run; one trainer made it return that one
    whatever had been asked for, so a request for a ten-epoch model could be
    answered with a different trainer's checkpoint -- and the caller would then
    "skip training, checkpoint already exists" and hand back a model nobody
    asked for. The trainer name is known at every call site; use it.
    """
    dataset_results = nnunet_results / dataset_folder
    if not dataset_results.is_dir():
        raise ValueError(f"nnUNet results folder not found: {dataset_results}")
    suffix = f"__nnUNetPlans__{configuration}"
    if trainer:
        exact = dataset_results / f"{trainer}{suffix}"
        if exact.is_dir():
            return exact
        present = sorted(p.name for p in dataset_results.iterdir() if p.is_dir())
        raise ValueError(
            f"no results for trainer {trainer!r} with configuration {configuration!r} "
            f"under {dataset_results}; present: {present}")
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


def fold_resumable(trainer_path: Path, fold: int) -> bool:
    """A training that stopped part-way and nnU-Net can pick up.

    nnU-Net writes checkpoint_latest.pth every 50 epochs and takes `--c` to
    continue from it. Without that flag an interrupted run silently starts again
    at epoch 0 -- on the 1000-epoch schedule this dataset needs, a machine
    hiccup at hour nine costs all nine hours.
    """
    fold_dir = trainer_path / f"fold_{fold}"
    return ((fold_dir / "checkpoint_latest.pth").is_file()
            and not (fold_dir / "checkpoint_final.pth").is_file())


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
    trained_folds: list[int] = []
    trainer_class = (trainer or DEFAULT_TRAINER).strip()

    # Only THIS trainer's checkpoints may satisfy "already trained": another
    # trainer's fold_0 is a different model, and reusing it would answer the
    # program's question with someone else's answer.
    current_trainer: Path | None = None
    try:
        current_trainer = trainer_dir(results_root, folder, configuration, trainer_class)
    except ValueError:
        pass

    train_device = "cpu" if device in {"cpu", "none"} else "cuda"

    for fold in range(nfolds):
        if current_trainer is not None and fold_complete(current_trainer, fold):
            logger.info("Skipping train fold %s (checkpoint already exists)", fold)
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
        if current_trainer is not None and fold_resumable(current_trainer, fold):
            logger.info("Resuming train fold %s from checkpoint_latest", fold)
            train_cmd.append("--c")
        run_cli(train_cmd, cwd=work_root, env=env, step=f"train fold {fold}")
        trained_folds.append(fold)

    resolved_trainer = trainer_dir(results_root, folder, configuration, trainer_class)
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


def _load_predictor_engine(model: dict[str, Any], resolved_device: str,
                           fold_list: tuple[int, ...]) -> Any:
    """Build the nnU-Net predictor object itself (no registry, no handle)."""
    require_nnunet()
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor  # type: ignore

    work_root = Path(model["work_root"])
    _set_nnunet_env(work_root)

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
    predictor.initialize_from_trained_model_folder(
        str(model["trainer_dir"]),
        use_folds=fold_list,
        checkpoint_name="checkpoint_final.pth",
    )
    return predictor


def create_predictor(
    model: dict[str, Any],
    *,
    device: str | None = None,
    folds: list[int] | None = None,
) -> dict[str, Any]:
    """Load an nnU-Net predictor once for repeated image inference."""
    resolved_device = str(device or model.get("device", "cpu")).lower()
    fold_list = tuple(folds if folds is not None else model.get("trained_folds", (0,)))
    predictor = _load_predictor_engine(model, resolved_device, fold_list)
    return {
        "vox_kind": PREDICTOR_KIND,
        "predictor_id": store_predictor(predictor),
        "model": model,
        "device": resolved_device,
        "folds": list(fold_list),
    }


def _predictor_engine(handle: dict[str, Any]) -> Any:
    """The live predictor for a handle, rebuilding it if this process lacks it.

    Call this holding ``predictor_lock(predictor_id)``: the rebuild must be as
    exclusive as the inference it precedes.

    A handle names process-local state by id, but it is also a VALUE: the engine
    content-addresses it, persists it, and hands it back on a later run -- where
    the registry is empty and every predict died with "predictor <id> is not
    available in this process". The id alone was never the point: the handle
    also carries the model (trainer directory, folds, device), which is all it
    takes to load the predictor again. So a miss reloads and re-registers under
    the SAME id, and the handle means the same thing in any process.
    """
    predictor_id = str(handle.get("predictor_id", "")).strip()
    if not predictor_id:
        raise ValueError("predictor handle is missing predictor_id")
    if not predictor_registered(predictor_id):
        model = handle["model"]
        resolved_device = str(handle.get("device") or model.get("device", "cpu")).lower()
        fold_list = tuple(handle.get("folds") or model.get("trained_folds", (0,)))
        logger.info("Reloading nnU-Net predictor %s from %s", predictor_id, model["trainer_dir"])
        store_predictor(_load_predictor_engine(model, resolved_device, fold_list), predictor_id)
    return load_predictor(predictor_id)


def predict_image(predictor_handle: dict[str, Any], volumes: Any) -> Any:
    """Run nnU-Net inference on one case and return a segmentation image."""
    from voxlogica.primitives.nnunet.cases import normalize_modality_volumes

    predictor_id = str(predictor_handle.get("predictor_id", "")).strip()
    if not predictor_id:
        raise ValueError("predictor handle is missing predictor_id")

    model = predictor_handle["model"]
    modality_volumes = normalize_modality_volumes(
        volumes,
        expected=len(model["modalities"]),
        name="image",
    )
    array, properties = volumes_to_nnunet_array(modality_volumes)
    # The lock covers the RELOAD as well as the inference. Reloading outside it
    # let two workers that both found the registry empty each build a predictor
    # and load a second copy of the weights onto the GPU -- the same "who is
    # inside this object" question the lock exists to answer. Preparing the
    # input above needs no lock.
    with predictor_lock(predictor_id):
        predictor = _predictor_engine(predictor_handle)
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
