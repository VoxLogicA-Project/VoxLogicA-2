"""nnUNet primitives: sequence-based train and predictor-based inference."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.nnunet import materialize as mat
from voxlogica.primitives.nnunet import runtime
from voxlogica.primitives.nnunet.cases import (
    DEFAULT_LABELS,
    normalize_configurations,
    DEFAULT_TRAINER,
    as_list,
    infer_modalities,
    is_model,
    is_predictor,
    normalize_modalities,
    build_model,
    parse_training_case,
    parse_training_cases,
)

logger = logging.getLogger(__name__)


def _arg(kwargs: dict[str, Any], key: str, default: Any = None) -> Any:
    return kwargs.get(key, default)


def _require_str(kwargs: dict[str, Any], key: str, name: str) -> str:
    value = str(_arg(kwargs, key, "")).strip()
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_int(kwargs: dict[str, Any], key: str, name: str, default: int) -> int:
    try:
        return int(float(_arg(kwargs, key, default)))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} must be int-like: {_arg(kwargs, key, default)!r}") from exc


def _optional_bool(kwargs: dict[str, Any], key: str, default: bool) -> bool:
    """A program's yes/no, written as a number or as a word."""
    if key not in kwargs or _arg(kwargs, key) is None:
        return default
    value = _arg(kwargs, key)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1", "1.0"}:
        return True
    if text in {"false", "no", "off", "0", "0.0"}:
        return False
    raise ValueError(f"expected true or false, got {value!r}")


def _optional_str(kwargs: dict[str, Any], key: str, default: str = "") -> str:
    if key not in kwargs or _arg(kwargs, key) is None:
        return default
    return str(_arg(kwargs, key)).strip()


#: Layout entries that are filesystem paths. A layout crosses the graph as a
#: plain value, so paths go out as strings and come back as `Path` here.
_PATH_KEYS = frozenset({"nnunet_raw", "nnunet_preprocessed", "nnunet_results",
                        "dataset_dir"})


def prepare_dataset(**kwargs: Any) -> dict[str, Any]:
    """Create the empty nnU-Net raw dataset. Arguments: work_root, dataset_name.

    Deliberately takes no cases: this is the step the per-case writes hang off,
    and it must not depend on a single image.
    """
    try:
        work_root = Path(_require_str(kwargs, "0", "work_root"))
        dataset_name = _require_str(kwargs, "1", "dataset_name") if "1" in kwargs else "VoxLogicA"
        dataset_id = mat.allocate_dataset_id(work_root)
        layout = mat.prepare_training_dataset(
            work_root=work_root, dataset_id=dataset_id, dataset_name=dataset_name
        )
        return {str(k): (str(v) if isinstance(v, Path) else v) for k, v in layout.items()}
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet prepare_dataset failed: %s", exc)
        raise ValueError(f"nnUNet prepare_dataset failed: {exc}") from exc


def write_case(**kwargs: Any) -> str:
    """Write ONE case into a prepared dataset. Arguments: dataset, case, modalities.

    This is the node whose completion lets an image die. Everything it is given
    is used once and dropped, so the images resident at any moment are those of
    the cases in flight, not those of the training set.
    """
    try:
        layout = _arg(kwargs, "0")
        if not isinstance(layout, dict) or "dataset_dir" not in layout:
            raise ValueError("write_case requires a dataset from nnunet.prepare_dataset")
        modalities = normalize_modalities(_arg(kwargs, "2"))
        case = parse_training_case(_arg(kwargs, "1"), modalities=modalities)
        return mat.write_training_case(layout, case)
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet write_case failed: %s", exc)
        raise ValueError(f"nnUNet write_case failed: {exc}") from exc


def finalize_dataset(**kwargs: Any) -> dict[str, Any]:
    """Close a written dataset. Arguments: dataset, modalities, written_ids.

    `written_ids` is the sequence the per-case writes returned. Passing it makes
    this step depend on all of them in the graph -- the dependency is an edge,
    not an ordering someone remembered to arrange -- and it carries the training
    count and the duplicate-id check with it.
    """
    try:
        layout = _arg(kwargs, "0")
        if not isinstance(layout, dict) or "dataset_dir" not in layout:
            raise ValueError("finalize_dataset requires a dataset from nnunet.prepare_dataset")
        modalities = normalize_modalities(_arg(kwargs, "1"))
        file_ids = [str(x) for x in as_list(_arg(kwargs, "2"), name="written_ids")]
        result = mat.finalize_training_dataset(
            layout=layout, modalities=modalities, file_ids=file_ids, labels=DEFAULT_LABELS
        )
        return {str(k): (str(v) if isinstance(v, Path) else v)
                for k, v in result["layout"].items()}
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet finalize_dataset failed: %s", exc)
        raise ValueError(f"nnUNet finalize_dataset failed: {exc}") from exc


def train_internal(**kwargs: Any) -> dict[str, Any]:
    """Train on a dataset already on disk. See `nnunet.train` in nnunet.imgql.

    The images are NOT arguments here. That is the whole point: this used to be
    `train(cases, ...)`, and taking every case as an argument meant every image
    had to be resident before the kernel ran -- 25.7 GB of them on a 309-case,
    4-modality run, which was killed at 51.4 GB RSS.
    """
    try:
        layout = _arg(kwargs, "0")
        if not isinstance(layout, dict) or "dataset_dir" not in layout:
            raise ValueError("train_internal requires a dataset from nnunet.finalize_dataset")
        layout = {k: (Path(v) if k in _PATH_KEYS else v) for k, v in layout.items()}
        modalities = normalize_modalities(_arg(kwargs, "1"))
        # Argument 2 may name ONE configuration or several. Several is the
        # documented workflow: nnU-Net plans 2d, 3d_fullres and 3d_lowres,
        # trains each, and picks -- so a program that wants that answer has to
        # be able to ask for it.
        configurations = (
            normalize_configurations(_arg(kwargs, "2")) if "2" in kwargs else ["2d"]
        )
        nfolds = _require_int(kwargs, "3", "nfolds", 5)
        device = str(_arg(kwargs, "4", "cpu")).lower()
        trainer = _optional_str(kwargs, "5", DEFAULT_TRAINER) or DEFAULT_TRAINER
        # Argument 6: the plans identifier, which is how nnU-Net selects an
        # architecture preset -- "nnUNetResEncUNetLPlans" for the residual
        # encoder presets, the default otherwise. It belongs in the program
        # rather than in the environment: which network was trained is part of
        # what an experiment says it did, and the cache key must change with it.
        plans = _optional_str(kwargs, "6", runtime.DEFAULT_PLANS) or runtime.DEFAULT_PLANS
        # Argument 7: whether to run nnU-Net's own postprocessing step, which
        # the documented workflow runs between training and inference and which
        # is therefore on by default. It is a parameter because it is a claim
        # about the result -- a program that wants the raw network output must
        # be able to say so, and to say so where the reader can see it.
        postprocess = _optional_bool(kwargs, "7", True)
        # Argument 8: a checkpoint to start from instead of random init. This
        # is how a program says "pretrained on the oracle" -- the weights are an
        # input to the experiment, so they belong in the expression that keys it.
        pretrained = _optional_str(kwargs, "8", "")

        if nfolds <= 0:
            raise ValueError("nfolds must be >= 1")

        return runtime.train_model(
            layout=layout,
            dataset_id=int(layout["dataset_id"]),
            dataset_name=str(layout["dataset_name"]),
            configurations=configurations,
            modalities=modalities,
            nfolds=nfolds,
            device=device,
            labels=DEFAULT_LABELS,
            trainer=trainer,
            plans=plans,
            postprocess=postprocess,
            pretrained=pretrained,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet training failed: %s", exc)
        raise ValueError(f"nnUNet training failed: {exc}") from exc


def load_model(**kwargs: Any) -> dict[str, Any]:
    """A model ALREADY on disk, as a value. Arguments: work_root, configuration,
    trainer, device.

    Why this exists: the only way to name an earlier model used to be to restate
    the `nnunet.train(...)` that produced it, which walks the whole training
    path -- and that path decides "already trained" from the trainer DIRECTORY,
    not from the training data (issue #57). Restating an expression that differs
    even slightly therefore risks being handed a model trained on other cases,
    silently. Naming the model outright cannot do that: it goes nowhere near
    training, writes nothing, and fails loudly if the checkpoints are absent.

    Everything the handle needs is recoverable: the work root's manifest records
    the dataset id, folder, modalities and labels, and the trained folds are the
    checkpoints that are actually there.
    """
    try:
        work_root = Path(_require_str(kwargs, "0", "work_root"))
        configuration = _require_str(kwargs, "1", "configuration") if "1" in kwargs else "3d_fullres"
        trainer = _optional_str(kwargs, "2", DEFAULT_TRAINER) or DEFAULT_TRAINER
        device = str(_arg(kwargs, "3", "cpu")).lower()
        plans = _optional_str(kwargs, "4", runtime.DEFAULT_PLANS) or runtime.DEFAULT_PLANS

        state = mat.load_state(work_root)
        if not state:
            raise ValueError(f"no nnU-Net manifest under {work_root}; nothing was trained there")
        roots = mat.nnunet_roots(work_root)
        directory = runtime.trainer_dir(roots["nnunet_results"], str(state["dataset_folder"]),
                                        configuration, trainer, plans)
        folds = [fold for fold in range(5) if runtime.fold_complete(directory, fold)]
        if not folds:
            raise ValueError(f"no completed fold under {directory}")

        model = build_model(
            work_root=str(work_root),
            dataset_id=int(state["dataset_id"]),
            dataset_folder=str(state["dataset_folder"]),
            configuration=configuration,
            modalities=list(state.get("modalities") or []),
            trained_folds=folds,
            trainer_dir=str(directory),
            labels=state.get("labels"),
            device=device,
            trainer=trainer,
        )
        model["postprocessing"] = runtime.resolve_postprocessing(model)
        return model
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet load_model failed: %s", exc)
        raise ValueError(f"nnUNet load_model failed: {exc}") from exc


def make_predictor(**kwargs: Any) -> dict[str, Any]:
    """Load an nnU-Net predictor from a trained model handle."""
    try:
        model = _arg(kwargs, "0")
        if not is_model(model):
            raise ValueError("make_predictor requires a model handle from nnunet.train")

        device = _arg(kwargs, "1")
        folds_value = _arg(kwargs, "2")
        fold_list = None
        if folds_value is not None:
            fold_list = [int(fold) for fold in as_list(folds_value, name="folds")]

        # Arguments 3-5: nnU-Net's own inference knobs, which the documented
        # nnUNetv2_predict exposes and this namespace did not. Defaults are
        # nnU-Net's, so saying nothing keeps the documented behaviour.
        step_size = float(_arg(kwargs, "3", runtime.DEFAULT_STEP_SIZE))
        tta = _optional_bool(kwargs, "4", True)
        checkpoint = _optional_str(kwargs, "5", runtime.DEFAULT_CHECKPOINT)

        return runtime.create_predictor(
            model,
            device=str(device).lower() if device is not None else None,
            folds=fold_list,
            step_size=step_size,
            tta=tta,
            checkpoint=checkpoint or runtime.DEFAULT_CHECKPOINT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet make_predictor failed: %s", exc)
        raise ValueError(f"nnUNet make_predictor failed: {exc}") from exc


def predict(**kwargs: Any) -> Any:
    """Segment one case from a loaded predictor and return a label image."""
    try:
        predictor = _arg(kwargs, "0")
        if not is_predictor(predictor):
            raise ValueError("predict requires a predictor handle from nnunet.make_predictor")
        if "1" not in kwargs:
            raise ValueError("predict requires an image or modality volume list as argument 1")
        return runtime.predict_image(predictor, _arg(kwargs, "1"))
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet prediction failed: %s", exc)
        raise ValueError(f"nnUNet prediction failed: {exc}") from exc


def env_check(**_kwargs: Any) -> dict[str, Any]:
    return runtime.env_check()


def get_primitives() -> dict[str, Callable[..., Any]]:
    return {
        "load_model": load_model,
        "prepare_dataset": prepare_dataset,
        "write_case": write_case,
        "finalize_dataset": finalize_dataset,
        "train_internal": train_internal,
        "make_predictor": make_predictor,
        "predict": predict,
        "env_check": env_check,
    }


def list_primitives() -> dict[str, str]:
    return {name: "nnUNet primitive" for name in get_primitives()}


def register_specs() -> dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    arities = {
        "load_model": AritySpec(min_args=1, max_args=5),
        "prepare_dataset": AritySpec(min_args=1, max_args=2),
        "write_case": AritySpec.fixed(3),
        "finalize_dataset": AritySpec.fixed(3),
        "train_internal": AritySpec(min_args=2, max_args=9),
        "make_predictor": AritySpec(min_args=1, max_args=6),
        "predict": AritySpec.fixed(2),
        "env_check": AritySpec.variadic(0),
    }
    descriptions = {
        "load_model": "Name a model already trained on disk, without training",
        "prepare_dataset": "Create an empty nnU-Net raw dataset",
        "write_case": "Write one training case into a prepared dataset",
        "finalize_dataset": "Close a written dataset (dataset.json, checks)",
        "train_internal": "Train on a dataset already on disk; see nnunet.train",
        "make_predictor": "Load an nnU-Net predictor from a trained model handle",
        "predict": "Segment one image with a loaded nnU-Net predictor",
        "env_check": "Inspect nnUNet and torch runtime environment",
    }
    specs: dict[str, tuple[PrimitiveSpec, Callable[..., Any]]] = {}
    for name, kernel in get_primitives().items():
        qualified = f"nnunet.{name}"
        specs[name] = (
            PrimitiveSpec(
                name=name,
                namespace="nnunet",
                kind="scalar",
                arity=arities[name],
                attrs_schema={},
                planner=default_planner_factory(qualified, kind="scalar"),
                kernel_name=qualified,
                description=descriptions[name],
            ),
            kernel,
        )
    return specs


def register_primitives() -> dict[str, Callable[..., Any]]:
    return get_primitives()
