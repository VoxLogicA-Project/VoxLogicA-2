"""nnUNet primitive namespace."""

from voxlogica.primitives.nnunet import predictor_registry
from voxlogica.primitives.nnunet.kernels import (
    env_check,
    get_primitives,
    list_primitives,
    make_predictor,
    finalize_dataset,
    predict,
    prepare_dataset,
    register_primitives,
    register_specs,
    train_internal,
    write_case,
)


def reset_runtime_state() -> None:
    predictor_registry.reset_runtime_state()


__all__ = [
    "env_check",
    "get_primitives",
    "list_primitives",
    "make_predictor",
    "finalize_dataset",
    "predict",
    "prepare_dataset",
    "register_primitives",
    "register_specs",
    "reset_runtime_state",
    "train_internal",
    "write_case",
]
