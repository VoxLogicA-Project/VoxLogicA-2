"""Two threads must never be inside one nnU-Net predictor at the same time.

An nnU-Net predictor is stateful: each call moves the network onto the device
and keeps per-call state on the object. The engine schedules independent
`nnunet.predict` nodes in parallel by design, and under free-threading nothing
serialises them by accident any more, so ten cases died with

    terminate called after throwing an instance of 'c10::Error'
      what():  invalid device pointer: 0x7e10723a1000
    Exception raised from free at c10/cuda/CUDACachingAllocator.cpp

with torch's `Module._apply` on the Python stack -- two threads moving the same
network onto the GPU at once.
"""

from __future__ import annotations

import threading

import pytest

from voxlogica.primitives.nnunet import predictor_registry as registry
from voxlogica.primitives.nnunet.runtime import predict_image


class OverlapDetectingPredictor:
    """Records whether a second thread ever entered while one was inside."""

    def __init__(self):
        self.inside = 0
        self.overlapped = False
        self.calls = 0
        self._guard = threading.Lock()

    def predict_single_npy_array(self, array, properties, *_rest):
        with self._guard:
            self.inside += 1
            self.calls += 1
            if self.inside > 1:
                self.overlapped = True
        # Long enough that unsynchronised threads would certainly overlap.
        threading.Event().wait(0.02)
        with self._guard:
            self.inside -= 1
        return array[0]


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset_runtime_state()
    yield
    registry.reset_runtime_state()


def test_concurrent_predicts_on_one_predictor_do_not_overlap():
    import numpy as np
    import SimpleITK as sitk

    engine = OverlapDetectingPredictor()
    handle = {
        "vox_kind": "nnunet_predictor",
        "predictor_id": registry.store(engine),
        "model": {"modalities": ["intensity"]},
    }
    image = sitk.GetImageFromArray(np.zeros((2, 3, 4), dtype=np.float32))

    threads = [threading.Thread(target=predict_image, args=(handle, image)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert engine.calls == 8
    assert engine.overlapped is False


def test_two_predictors_get_two_locks():
    first, second = registry.store(object()), registry.store(object())

    assert registry.lock_for(first) is not registry.lock_for(second)
    assert registry.lock_for(first) is registry.lock_for(first)
