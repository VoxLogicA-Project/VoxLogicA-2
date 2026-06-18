"""nnUNet primitive namespace."""

from voxlogica.primitives.nnunet import predictor_registry
from voxlogica.primitives.nnunet.kernels import (
    env_check,
    get_primitives,
    list_primitives,
    make_predictor,
    predict,
    register_primitives,
    register_specs,
    train,
)


def reset_runtime_state() -> None:
    predictor_registry.reset_runtime_state()


__all__ = [
    "env_check",
    "get_primitives",
    "list_primitives",
    "make_predictor",
    "predict",
    "register_primitives",
    "register_specs",
    "reset_runtime_state",
    "train",
]
