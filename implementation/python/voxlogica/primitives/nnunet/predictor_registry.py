"""In-process registry for loaded nnU-Net predictors."""

from __future__ import annotations

import uuid
from typing import Any

_REGISTRY: dict[str, Any] = {}


def store(predictor: Any) -> str:
    """Store a predictor engine and return an opaque process-local id."""
    predictor_id = uuid.uuid4().hex
    _REGISTRY[predictor_id] = predictor
    return predictor_id


def load(predictor_id: str) -> Any:
    """Return a predictor previously stored in this process."""
    try:
        return _REGISTRY[predictor_id]
    except KeyError as exc:
        raise ValueError(f"nnUNet predictor {predictor_id!r} is not available in this process") from exc


def reset_runtime_state() -> None:
    """Drop loaded predictors between program runs."""
    _REGISTRY.clear()
