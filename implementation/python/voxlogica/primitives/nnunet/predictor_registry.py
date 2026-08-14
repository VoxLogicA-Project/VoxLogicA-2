"""In-process registry for loaded nnU-Net predictors."""

from __future__ import annotations

import threading
import uuid
from typing import Any

_REGISTRY: dict[str, Any] = {}
# One lock per predictor. An nnU-Net predictor is a STATEFUL object: every
# predict call moves the network onto the device and keeps per-call state on
# self, so two threads inside one predictor corrupt each other. Under
# free-threading nothing serialises them by accident any more, and the engine
# schedules independent predict nodes in parallel by design -- ten cases died
# with `terminate called after throwing an instance of 'c10::Error'`,
# `what(): invalid device pointer`, raised from CUDACachingAllocator::free with
# torch's Module._apply on the Python stack.
#
# Serialising is not a loss here: inference runs on one GPU, which a single
# predict already saturates.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


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


def lock_for(predictor_id: str) -> threading.Lock:
    """The lock that serialises inference on one predictor."""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(predictor_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[predictor_id] = lock
        return lock


def reset_runtime_state() -> None:
    """Drop loaded predictors between program runs."""
    _REGISTRY.clear()
    with _LOCKS_GUARD:
        _LOCKS.clear()
