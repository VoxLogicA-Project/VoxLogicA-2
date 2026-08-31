"""nnUNet CLI orchestration."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
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


#: nnU-Net selects an architecture preset through its PLANS, not through the
#: trainer: the ResEnc presets are a different planner at preprocessing time and
#: a different plans identifier at training time, and they land in a differently
#: named results directory. Each entry maps the plans identifier a caller asks
#: for to the planner that produces it; the default needs no planner flag.
PLANS_PLANNER = {
    "nnUNetResEncUNetMPlans": "nnUNetPlannerResEncM",
    "nnUNetResEncUNetLPlans": "nnUNetPlannerResEncL",
    "nnUNetResEncUNetXLPlans": "nnUNetPlannerResEncXL",
}

DEFAULT_PLANS = "nnUNetPlans"

#: nnU-Net's own inference defaults, which are what nnUNetv2_predict uses when
#: told nothing: half-patch sliding window, test-time augmentation by mirroring
#: on, and the final rather than the best checkpoint. Named here so a program can
#: depart from them deliberately and so the departure is visible in the handle.
DEFAULT_STEP_SIZE = 0.5
DEFAULT_CHECKPOINT = "checkpoint_final.pth"


def trainer_dir(nnunet_results: Path, dataset_folder: str, configuration: str,
                trainer: str | None = None, plans: str = DEFAULT_PLANS) -> Path:
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
    suffix = f"__{plans or DEFAULT_PLANS}__{configuration}"
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


# ---------------------------------------------------------------------------
# Postprocessing: nnU-Net's own last documented step, which this module skipped
# ---------------------------------------------------------------------------
# The documented workflow is train -> nnUNetv2_find_best_configuration ->
# nnUNetv2_apply_postprocessing -> predict. The middle step asks, on the fold's
# own validation predictions, whether keeping only the largest connected
# component scores better than the raw output, and inference then applies what
# it decided. Skipping it is not a neutral simplification: it leaves the
# spurious-component failures that step exists to remove.
#
# The decision is a property of a trained model, so it is computed once, cached
# on disk where nnU-Net itself caches it (postprocessing.pkl next to the
# validation predictions), and carried on the model handle.

_PP_LOCK = threading.Lock()
_PP_RESOLVED: dict[str, dict[str, Any] | None] = {}


def postprocessing_pkl(trainer_path: Path, fold: int) -> Path:
    """Where nnU-Net itself leaves the decision it made for one fold."""
    return trainer_path / f"fold_{fold}" / "validation" / "postprocessing.pkl"


def _decision_from_pickle(path: Path) -> dict[str, Any] | None:
    """Read the decision nnU-Net left on disk, as values a model can carry.

    Function objects cannot travel on a model handle -- the handle is a value
    the engine content-addresses and persists -- so the operations are named
    here and looked back up when they are applied.
    """
    if not path.is_file():
        return None
    try:
        from batchgenerators.utilities.file_and_folder_operations import load_pickle  # type: ignore

        pp_fns, pp_kwargs = load_pickle(str(path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s is unreadable (%s)", path.name, exc)
        return None
    return {
        "operations": [getattr(fn, "__name__", str(fn)) for fn in pp_fns],
        "kwargs": [dict(kw) for kw in pp_kwargs],
    }


def determine_postprocessing_for(trainer_path: Path, labels_dir: Path,
                                 folds: list[int], work_root: Path) -> dict[str, Any] | None:
    """Ask nnU-Net whether postprocessing helps this model, the way it asks itself.

    Runs ``nnUNetv2_determine_postprocessing`` as a child process, like every
    other nnU-Net step here. That is not only for consistency: the function
    behind that command builds a multiprocessing pool, and under Python 3.14 the
    default start method re-imports the parent's ``__main__`` -- which, in a
    VoxLogicA run, is the VoxLogicA run. Measured: called in-process it failed
    with the freeze_support bootstrap error and re-entered the caller several
    times before one attempt got through. A child process cannot do that.

    Returns the decision -- possibly ``{"operations": []}``, which is a real
    answer meaning the raw output was already best -- or ``None`` when the
    question cannot be put (no validation predictions, no reference labels, the
    command failing). A failure here must never fail a training: postprocessing
    is an improvement, not a correctness requirement.
    """
    # Absolute, because the command runs with cwd=work_root: a relative path a
    # caller passed in would be read from somewhere else entirely, and the child
    # would fail on a file that is plainly there.
    trainer_path = Path(trainer_path).resolve()
    labels_dir = Path(labels_dir).resolve()
    work_root = Path(work_root).resolve()

    fold = folds[0] if folds else 0
    cached = postprocessing_pkl(trainer_path, fold)
    decision = _decision_from_pickle(cached)
    if decision is not None:
        return decision

    validation = cached.parent
    plans = trainer_path / "plans.json"
    dataset_json = trainer_path / "dataset.json"
    if not (validation.is_dir() and labels_dir.is_dir()
            and plans.is_file() and dataset_json.is_file()):
        logger.info("postprocessing: nothing to determine from (%s)", validation)
        return None

    try:
        _set_nnunet_env(work_root)
        run_cli(
            [
                nnunet_command("nnUNetv2_determine_postprocessing"),
                "-i", str(validation),
                "-ref", str(labels_dir),
                "-plans_json", str(plans),
                "-dataset_json", str(dataset_json),
                "-np", "4",
                "--remove_postprocessed",
            ],
            cwd=work_root, env=nnunet_env(), step="postprocessing",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("postprocessing could not be determined (%s); predicting raw", exc)
        return None

    decision = _decision_from_pickle(cached)
    if decision is None:
        logger.warning("postprocessing left no decision in %s; predicting raw", cached)
        return None
    logger.info("postprocessing determined: %s",
                decision["operations"] or "none (raw output is best)")
    return decision


def model_labels_dir(model: dict[str, Any]) -> Path:
    """The reference labels a model was trained against, from the model alone."""
    return (Path(model["work_root"]) / "nnUNet_raw"
            / str(model["dataset_folder"]) / "labelsTr")


def resolve_postprocessing(model: dict[str, Any]) -> dict[str, Any] | None:
    """The decision for a model, determining it if the model predates this step.

    A model trained before postprocessing existed here carries no ``"postprocessing"``
    key at all, and it should not have to be retrained to gain one: everything
    the decision needs is the trained fold's own validation output, which is
    still on disk. A key that is present and ``None`` means the question was
    already put and could not be answered -- do not ask again on every case.

    Answers None for anything it cannot work out, including a model handle that
    does not carry the fields this needs. Postprocessing is an improvement, not
    a correctness requirement: a prediction that could have been postprocessed
    and was not is a slightly worse prediction, while a prediction that fails is
    no prediction at all.
    """
    if "postprocessing" in model:
        return model.get("postprocessing")
    if len(model.get("selection") or []) > 1:
        # An ensemble's decision is nnU-Net's own, made over the ensembled
        # cross-validation and left beside it, not beside either member. There
        # is nothing to determine here from one trainer directory.
        return None
    cache_key = str(model.get("trainer_dir", ""))
    with _PP_LOCK:
        if cache_key in _PP_RESOLVED:
            return _PP_RESOLVED[cache_key]
        decision = None
        try:
            folds = [int(f) for f in model.get("trained_folds", (0,))] or [0]
            decision = determine_postprocessing_for(
                Path(model["trainer_dir"]), model_labels_dir(model), folds,
                Path(model["work_root"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("postprocessing not resolved for this model (%s); predicting raw", exc)
        _PP_RESOLVED[cache_key] = decision
        return decision


def apply_postprocessing_to_array(segmentation: Any, decision: dict[str, Any] | None) -> Any:
    """Apply what determine_postprocessing_for decided, to one segmentation."""
    if not decision or not decision.get("operations"):
        return segmentation
    try:
        from nnunetv2.postprocessing.remove_connected_components import (  # type: ignore
            remove_all_but_largest_component_from_segmentation,
        )
    except Exception:  # noqa: BLE001
        return segmentation
    known = {"remove_all_but_largest_component_from_segmentation":
             remove_all_but_largest_component_from_segmentation}
    out = segmentation
    for name, kwargs in zip(decision["operations"], decision.get("kwargs", [])):
        fn = known.get(name)
        if fn is None:
            # Named by a future nnU-Net and not implemented here. Say so: a
            # silently unapplied step is a result that quietly is not the one
            # the model was measured with.
            logger.warning("postprocessing step %r is not applied: unknown here", name)
            continue
        out = fn(out, **kwargs)
    return out


def find_best_configuration_for(*, dataset_id: int, results_root: Path, folder: str,
                                configurations: list[str], plans_id: str,
                                trainer_class: str, folds: list[int],
                                work_root: Path, env: dict[str, str]
                                ) -> tuple[list[dict[str, str]], str]:
    """Let nnU-Net choose among the configurations that were trained.

    This is `nnUNetv2_find_best_configuration`: it scores every trained
    configuration on the cross-validation, tries ensembling them, and names the
    single model or the pair that scored best. Without it, training several
    configurations only produces several models and no answer about which to
    believe -- the choice would fall to whoever wrote the program, on no evidence.

    Returns the selected members as ``{configuration, plans, trainer}`` together
    with the postprocessing file nnU-Net decided for that selection -- for an
    ensemble that decision is made over the ensembled cross-validation and
    exists nowhere else. An empty list means the question could not be answered,
    and the caller falls back to the first configuration, which is what the
    program asked for first.
    """
    cmd = [
        nnunet_command("nnUNetv2_find_best_configuration"),
        str(dataset_id),
        "-c", *configurations,
        "-p", plans_id,
        "-tr", trainer_class or DEFAULT_TRAINER,
        "-f", *[str(f) for f in (folds or [0])],
        "-np", "4",
    ]
    try:
        run_cli(cmd, cwd=work_root, env=env, step="find best configuration")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not choose a configuration (%s); using %s",
                       exc, configurations[0])
        return [], ""

    info = results_root / folder / "inference_information.json"
    if not info.is_file():
        logger.warning("no inference_information.json in %s; using %s",
                       info.parent, configurations[0])
        return [], ""
    try:
        chosen = json.loads(info.read_text(encoding="utf-8"))
        best = chosen["best_model_or_ensemble"]
        selection = [{"configuration": str(m["configuration"]),
                      "plans": str(m["plans"]),
                      "trainer": str(m["trainer"])}
                     for m in best["selected_model_or_models"]]
        pp_file = str(best.get("postprocessing_file") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("inference_information.json unreadable (%s); using %s",
                       exc, configurations[0])
        return [], ""

    logger.info("chosen: %s", " + ".join(m["configuration"] for m in selection))
    return selection, pp_file


def train_model(
    *,
    layout: dict[str, Any],
    dataset_id: int,
    dataset_name: str,
    configurations: list[str],
    modalities: list[str],
    nfolds: int,
    device: str,
    labels: dict[str, int],
    trainer: str = DEFAULT_TRAINER,
    plans: str = DEFAULT_PLANS,
    postprocess: bool = True,
    pretrained: str = "",
) -> dict[str, Any]:
    require_nnunet()
    work_root = Path(layout["work_dir"])
    _set_nnunet_env(work_root)
    env = nnunet_env()
    if device in {"cpu", "none"}:
        env["CUDA_VISIBLE_DEVICES"] = ""

    # Absolute, and checked here rather than eight minutes into a training: the
    # command runs with cwd=work_root, so a relative path would be read from
    # somewhere else, and nnU-Net's own failure for a missing file arrives after
    # preprocessing has already been paid for.
    pretrained_path = ""
    if pretrained:
        candidate = Path(pretrained).expanduser().resolve()
        if not candidate.is_file():
            raise ValueError(f"pretrained weights not found: {candidate}")
        pretrained_path = str(candidate)

    plans_id = (plans or DEFAULT_PLANS).strip() or DEFAULT_PLANS
    plan_cmd = [
        nnunet_command("nnUNetv2_plan_and_preprocess"),
        "-d",
        str(dataset_id),
        "-c",
        *configurations,
        "--verify_dataset_integrity",
    ]
    # A non-default plans identifier needs its planner named at preprocessing
    # time, otherwise nnU-Net writes nnUNetPlans and training then fails to find
    # what it was asked for. An unknown identifier is passed through to the
    # trainer anyway: the caller may have produced it by other means, and
    # refusing here would be this module deciding what nnU-Net supports.
    planner = PLANS_PLANNER.get(plans_id)
    if planner is not None:
        plan_cmd.extend(["-pl", planner])
    run_cli(plan_cmd, cwd=work_root, env=env, step="plan")

    results_root = Path(layout["nnunet_results"])
    folder = str(layout["dataset_folder"])
    trainer_class = (trainer or DEFAULT_TRAINER).strip()
    train_device = "cpu" if device in {"cpu", "none"} else "cuda"

    # Softmax is saved only when there is something to ensemble WITH. It is
    # large -- one array per validation case -- and nnUNetv2_find_best_configuration
    # needs it to try ensembles, so the cost is paid exactly when it buys
    # something.
    save_probabilities = len(configurations) > 1
    trained_folds: list[int] = []

    for configuration in configurations:
        # Only THIS trainer's checkpoints may satisfy "already trained": another
        # trainer's fold_0 is a different model, and reusing it would answer the
        # program's question with someone else's answer.
        current_trainer: Path | None = None
        try:
            current_trainer = trainer_dir(results_root, folder, configuration,
                                          trainer_class, plans_id)
        except ValueError:
            pass

        for fold in range(nfolds):
            if current_trainer is not None and fold_complete(current_trainer, fold):
                logger.info("Skipping %s fold %s (checkpoint already exists)",
                            configuration, fold)
                if fold not in trained_folds:
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
            if plans_id != DEFAULT_PLANS:
                train_cmd.extend(["-p", plans_id])
            if save_probabilities:
                train_cmd.append("--npz")
            if pretrained_path:
                # nnU-Net applies this only when it actually trains, and only if
                # the architecture matches: the weights must come from a model
                # built by the same plans and configuration, or it refuses.
                train_cmd.extend(["-pretrained_weights", pretrained_path])
            if current_trainer is not None and fold_resumable(current_trainer, fold):
                logger.info("Resuming %s fold %s from checkpoint_latest",
                            configuration, fold)
                train_cmd.append("--c")
            run_cli(train_cmd, cwd=work_root, env=env,
                    step=f"train {configuration} fold {fold}")
            if fold not in trained_folds:
                trained_folds.append(fold)

    trained_folds.sort()

    # With one configuration there is nothing to choose between, and asking
    # would cost a full cross-validation evaluation for a foregone answer.
    selection: list[dict[str, str]]
    chosen_pp_file = ""
    if len(configurations) > 1:
        selection, chosen_pp_file = find_best_configuration_for(
            dataset_id=dataset_id, results_root=results_root, folder=folder,
            configurations=configurations, plans_id=plans_id,
            trainer_class=trainer_class, folds=trained_folds,
            work_root=work_root, env=env)
    else:
        selection = []
    if not selection:
        selection = [{"configuration": configurations[0], "plans": plans_id,
                      "trainer": trainer_class}]
    for member in selection:
        member["trainer_dir"] = str(trainer_dir(
            results_root, folder, member["configuration"],
            member["trainer"], member["plans"]))

    configuration = selection[0]["configuration"]
    resolved_trainer = Path(selection[0]["trainer_dir"])

    # For an ensemble the decision was already made, over the ensembled
    # cross-validation, by the command that chose the ensemble; determining one
    # from a single member's validation would answer a different question.
    if not postprocess:
        postprocessing = None
    elif chosen_pp_file:
        postprocessing = _decision_from_pickle(Path(chosen_pp_file))
    else:
        postprocessing = determine_postprocessing_for(
            resolved_trainer, Path(layout["dataset_dir"]) / "labelsTr",
            trained_folds, work_root)
    state = load_state(work_root) or {}
    state.update(
        {
            "configuration": configuration,
            "configurations": list(configurations),
            "selection": selection,
            "trained_folds": trained_folds,
            "trainer_dir": str(resolved_trainer),
            "trainer": trainer_class,
            "plans": plans_id,
            "pretrained": pretrained_path,
            "postprocessing": postprocessing,
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
        postprocessing=postprocessing,
        pretrained=pretrained_path,
        configurations=list(configurations),
        selection=selection,
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
                           fold_list: tuple[int, ...], *,
                           step_size: float = DEFAULT_STEP_SIZE,
                           tta: bool = True,
                           checkpoint: str = DEFAULT_CHECKPOINT) -> Any:
    """Build the nnU-Net predictor object itself (no registry, no handle)."""
    require_nnunet()
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor  # type: ignore

    work_root = Path(model["work_root"])
    _set_nnunet_env(work_root)

    torch_device = _torch_device(resolved_device)
    perform_on_device = torch_device.type == "cuda"

    predictor = nnUNetPredictor(
        tile_step_size=step_size,
        use_gaussian=True,
        use_mirroring=tta,
        perform_everything_on_device=perform_on_device,
        device=torch_device,
        verbose=False,
        verbose_preprocessing=False,
        allow_tqdm=False,
    )
    predictor.initialize_from_trained_model_folder(
        str(model["trainer_dir"]),
        use_folds=fold_list,
        checkpoint_name=checkpoint,
    )
    return predictor


def model_members(model: dict[str, Any]) -> list[dict[str, str]]:
    """The models inference must run, one entry when nothing was ensembled."""
    selection = model.get("selection") or []
    if selection:
        return [dict(m) for m in selection]
    return [{"configuration": str(model.get("configuration", "")),
             "trainer_dir": str(model["trainer_dir"])}]


def create_predictor(
    model: dict[str, Any],
    *,
    device: str | None = None,
    folds: list[int] | None = None,
    step_size: float = DEFAULT_STEP_SIZE,
    tta: bool = True,
    checkpoint: str = DEFAULT_CHECKPOINT,
) -> dict[str, Any]:
    """Load an nnU-Net predictor once for repeated image inference.

    One predictor per selected model: nnUNetv2_find_best_configuration may name
    two, and an ensemble is only an ensemble if both of them run.

    The three inference knobs default to nnU-Net's own and are carried ON THE
    HANDLE, because the handle is what a later process rebuilds the predictor
    from: a knob kept only in this call would silently revert to the default the
    first time the registry is cold.
    """
    if not 0 < float(step_size) <= 1:
        raise ValueError(f"step_size must be in (0, 1]; got {step_size}")
    resolved_device = str(device or model.get("device", "cpu")).lower()
    fold_list = tuple(folds if folds is not None else model.get("trained_folds", (0,)))
    checkpoint_name = str(checkpoint or DEFAULT_CHECKPOINT)

    members = []
    for member in model_members(model):
        engine = _load_predictor_engine(
            {**model, "trainer_dir": member["trainer_dir"]},
            resolved_device, fold_list,
            step_size=float(step_size), tta=bool(tta), checkpoint=checkpoint_name)
        members.append({
            "predictor_id": store_predictor(engine),
            "trainer_dir": member["trainer_dir"],
            "configuration": member.get("configuration", ""),
        })

    return {
        "vox_kind": PREDICTOR_KIND,
        # The first member's id also stands as THE id of the handle, so a handle
        # of one member is byte-for-byte what it was before ensembling existed.
        "predictor_id": members[0]["predictor_id"],
        "members": members,
        "model": model,
        "device": resolved_device,
        "folds": list(fold_list),
        "step_size": float(step_size),
        "tta": bool(tta),
        "checkpoint": checkpoint_name,
    }


def _predictor_engine(handle: dict[str, Any],
                      member: dict[str, Any] | None = None) -> Any:
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
    member = member or {"predictor_id": handle.get("predictor_id", "")}
    predictor_id = str(member.get("predictor_id", "")).strip()
    if not predictor_id:
        raise ValueError("predictor handle is missing predictor_id")
    if not predictor_registered(predictor_id):
        model = handle["model"]
        if member.get("trainer_dir"):
            model = {**model, "trainer_dir": member["trainer_dir"]}
        resolved_device = str(handle.get("device") or model.get("device", "cpu")).lower()
        fold_list = tuple(handle.get("folds") or model.get("trained_folds", (0,)))
        logger.info("Reloading nnU-Net predictor %s from %s", predictor_id, model["trainer_dir"])
        store_predictor(
            _load_predictor_engine(
                model, resolved_device, fold_list,
                step_size=float(handle.get("step_size", DEFAULT_STEP_SIZE)),
                tta=bool(handle.get("tta", True)),
                checkpoint=str(handle.get("checkpoint") or DEFAULT_CHECKPOINT)),
            predictor_id)
    return load_predictor(predictor_id)


def handle_members(handle: dict[str, Any]) -> list[dict[str, Any]]:
    """The members of a predictor handle, tolerating handles that predate them.

    A handle is a persisted value: one written before ensembling existed has a
    single ``predictor_id`` and no ``members``, and must keep working.
    """
    members = handle.get("members")
    if members:
        return [dict(m) for m in members]
    return [{"predictor_id": handle.get("predictor_id", ""),
             "trainer_dir": handle.get("model", {}).get("trainer_dir", "")}]


def predict_image(predictor_handle: dict[str, Any], volumes: Any) -> Any:
    """Run nnU-Net inference on one case and return a segmentation image."""
    from voxlogica.primitives.nnunet.cases import normalize_modality_volumes

    members = handle_members(predictor_handle)
    if not members or not str(members[0].get("predictor_id", "")).strip():
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
    if len(members) == 1:
        predictor_id = str(members[0]["predictor_id"])
        with predictor_lock(predictor_id):
            predictor = _predictor_engine(predictor_handle, members[0])
            segmentation = predictor.predict_single_npy_array(
                array, properties, None, None, False)
    else:
        # An ensemble, the way nnU-Net ensembles: average the probabilities, not
        # the labels. Averaging labels would be a vote among two, which has no
        # tie-break and throws away how sure either model was. Both come back
        # resampled to the original geometry, so they are directly comparable
        # even when one model is 2d and the other 3d.
        summed = None
        label_manager = None
        for member in members:
            predictor_id = str(member["predictor_id"])
            with predictor_lock(predictor_id):
                predictor = _predictor_engine(predictor_handle, member)
                _, probabilities = predictor.predict_single_npy_array(
                    array, properties, None, None, True)
                label_manager = predictor.label_manager
            summed = probabilities if summed is None else summed + probabilities
        segmentation = label_manager.convert_probabilities_to_segmentation(
            summed / len(members))
        segmentation = _as_numpy(segmentation)

    # The documented workflow applies the postprocessing that
    # nnUNetv2_find_best_configuration selected; skipping it was this module
    # departing from nnU-Net's own protocol, silently and in the direction of a
    # worse result. It runs before the array becomes an image, because
    # nnU-Net's own function works on labels.
    segmentation = apply_postprocessing_to_array(segmentation, resolve_postprocessing(model))
    return segmentation_to_sitk(segmentation, properties)


def _as_numpy(array: Any) -> Any:
    """nnU-Net's label conversion may hand back a torch tensor."""
    return array.cpu().numpy() if hasattr(array, "cpu") else array


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
