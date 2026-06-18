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
    DEFAULT_TRAINER,
    as_list,
    infer_modalities,
    is_model,
    is_predictor,
    normalize_modalities,
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


def _optional_str(kwargs: dict[str, Any], key: str, default: str = "") -> str:
    if key not in kwargs or _arg(kwargs, key) is None:
        return default
    return str(_arg(kwargs, key)).strip()


def train(**kwargs: Any) -> dict[str, Any]:
    """Train nnUNet from [case_id, modalities, label] sequences."""
    try:
        raw_cases = _arg(kwargs, "0")
        if raw_cases is None:
            raise ValueError("train requires training_cases as argument 0")
        try:
            as_list(raw_cases, name="training_cases")
        except ValueError as exc:
            raise ValueError("train requires a training_cases sequence") from exc

        work_root = Path(_require_str(kwargs, "1", "work_root"))
        modalities_value = _arg(kwargs, "2")
        modalities = (
            infer_modalities(raw_cases)
            if modalities_value is None
            else normalize_modalities(modalities_value)
        )
        configuration = _require_str(kwargs, "3", "configuration") if "3" in kwargs else "2d"
        nfolds = _require_int(kwargs, "4", "nfolds", 5)
        dataset_name = _require_str(kwargs, "5", "dataset_name") if "5" in kwargs else "VoxLogicA"
        device = str(_arg(kwargs, "6", "cpu")).lower()
        trainer = _optional_str(kwargs, "7", DEFAULT_TRAINER) or DEFAULT_TRAINER
        labels = DEFAULT_LABELS

        if nfolds <= 0:
            raise ValueError("nfolds must be >= 1")

        cases = parse_training_cases(raw_cases, modalities=modalities)
        dataset_id = mat.allocate_dataset_id(work_root)
        materialized = mat.write_training_dataset(
            work_root=work_root,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            modalities=modalities,
            cases=cases,
            labels=labels,
        )
        return runtime.train_model(
            layout=materialized["layout"],
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            configuration=configuration,
            modalities=modalities,
            nfolds=nfolds,
            device=device,
            labels=labels,
            trainer=trainer,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("nnUNet training failed: %s", exc)
        raise ValueError(f"nnUNet training failed: {exc}") from exc


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

        return runtime.create_predictor(
            model,
            device=str(device).lower() if device is not None else None,
            folds=fold_list,
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
        "train": train,
        "make_predictor": make_predictor,
        "predict": predict,
        "env_check": env_check,
    }


def list_primitives() -> dict[str, str]:
    return {name: "nnUNet primitive" for name in get_primitives()}


def register_specs() -> dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    arities = {
        "train": AritySpec(min_args=2, max_args=8),
        "make_predictor": AritySpec(min_args=1, max_args=3),
        "predict": AritySpec.fixed(2),
        "env_check": AritySpec.variadic(0),
    }
    descriptions = {
        "train": "Train nnUNet from a case sequence",
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
