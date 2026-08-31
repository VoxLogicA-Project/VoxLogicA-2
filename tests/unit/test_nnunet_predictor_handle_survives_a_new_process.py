"""A predictor handle must still work in a process that never created it.

`nnunet.make_predictor` returns a handle naming process-local state by id. But
a handle is also a VALUE: the engine content-addresses it, persists it, and
hands it back on a later run -- where the registry is empty and every predict
died with

    nnUNet prediction failed: nnUNet predictor 'f129...' is not available in
    this process

The id was never the whole handle: it also carries the model (trainer
directory, folds, device), which is all it takes to load the predictor again.
A miss therefore reloads and re-registers under the same id.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.nnunet import predictor_registry as registry
from voxlogica.primitives.nnunet import runtime


class FakeEngine:
    def __init__(self):
        self.calls = 0

    def predict_single_npy_array(self, array, properties, *_rest):
        self.calls += 1
        return array[0]


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset_runtime_state()
    yield
    registry.reset_runtime_state()


def handle_for(predictor_id: str) -> dict:
    return {
        "vox_kind": "nnunet_predictor",
        "predictor_id": predictor_id,
        "model": {"modalities": ["intensity"], "trainer_dir": "/nowhere", "trained_folds": [0]},
        "device": "cpu",
        "folds": [0],
    }


def an_image():
    return sitk.GetImageFromArray(np.zeros((2, 3, 4), dtype=np.float32))


def test_a_handle_from_another_process_reloads_instead_of_failing(monkeypatch):
    # The registry is empty, exactly as it is at the start of a new run.
    rebuilt = FakeEngine()
    seen = {}

    def fake_load(model, device, folds, **knobs):
        seen["model"], seen["device"], seen["folds"] = model, device, folds
        return rebuilt

    monkeypatch.setattr(runtime, "_load_predictor_engine", fake_load)

    runtime.predict_image(handle_for("deadbeef"), an_image())

    assert rebuilt.calls == 1
    assert seen["device"] == "cpu"
    assert seen["folds"] == (0,)
    # Re-registered under the SAME id, so the next call is a plain hit.
    assert registry.has("deadbeef")
    assert registry.load("deadbeef") is rebuilt


def test_a_live_predictor_is_never_reloaded(monkeypatch):
    engine = FakeEngine()
    predictor_id = registry.store(engine)

    def refuse(*_args, **_kwargs):
        raise AssertionError("must not reload a predictor this process already has")

    monkeypatch.setattr(runtime, "_load_predictor_engine", refuse)

    runtime.predict_image(handle_for(predictor_id), an_image())

    assert engine.calls == 1


def test_concurrent_first_use_reloads_the_weights_exactly_once(monkeypatch):
    import threading

    builds = []
    engine = FakeEngine()

    def fake_load(model, device, folds, **knobs):
        builds.append(device)
        return engine

    monkeypatch.setattr(runtime, "_load_predictor_engine", fake_load)
    handle = handle_for("deadbeef")

    threads = [threading.Thread(target=runtime.predict_image, args=(handle, an_image()))
               for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Six predictions, one load of the weights: reloading is under the lock.
    assert engine.calls == 6
    assert len(builds) == 1


def test_a_handle_without_an_id_is_still_an_error():
    with pytest.raises(ValueError, match="predictor_id"):
        runtime.predict_image({"vox_kind": "nnunet_predictor", "predictor_id": " ",
                               "model": {"modalities": ["intensity"]}}, an_image())
