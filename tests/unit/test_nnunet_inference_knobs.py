"""nnUNetv2_predict's knobs, reachable from a program and surviving a reload.

Sliding-window step size, test-time augmentation and the choice of checkpoint
are documented options of nnU-Net's own inference command. This namespace had
them hardcoded at nnU-Net's defaults -- conformant, but a program could not say
otherwise, and could not record that it had.

The reload is the part worth pinning. A predictor handle is a VALUE: the engine
persists it and hands it back in a later process where the registry is empty,
and the predictor is rebuilt from the handle alone. A knob kept only in the
call that created it would quietly revert to the default exactly then, and the
second run would not be the experiment the first one was.
"""

from __future__ import annotations

import pytest

from voxlogica.primitives.nnunet import runtime


class FakePredictor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.folder = None
        self.checkpoint = None

    def initialize_from_trained_model_folder(self, folder, use_folds, checkpoint_name):
        self.folder, self.folds, self.checkpoint = folder, use_folds, checkpoint_name


@pytest.fixture
def capture(monkeypatch):
    built: list[FakePredictor] = []

    def fake_engine(model, device, folds, *, step_size, tta, checkpoint):
        p = FakePredictor(tile_step_size=step_size, use_mirroring=tta)
        p.initialize_from_trained_model_folder(model["trainer_dir"], folds, checkpoint)
        built.append(p)
        return p

    monkeypatch.setattr(runtime, "_load_predictor_engine", fake_engine)
    monkeypatch.setattr(runtime, "store_predictor", lambda p, pid=None: pid or "pid0")
    return built


MODEL = {"trainer_dir": "/w/t", "trained_folds": [0], "device": "cpu"}


def test_saying_nothing_keeps_nnunets_own_defaults(capture):
    handle = runtime.create_predictor(MODEL)

    assert capture[0].kwargs == {"tile_step_size": 0.5, "use_mirroring": True}
    assert capture[0].checkpoint == "checkpoint_final.pth"
    assert (handle["step_size"], handle["tta"], handle["checkpoint"]) == (
        0.5, True, "checkpoint_final.pth")


def test_the_knobs_reach_the_predictor(capture):
    runtime.create_predictor(MODEL, step_size=0.75, tta=False,
                             checkpoint="checkpoint_best.pth")

    assert capture[0].kwargs == {"tile_step_size": 0.75, "use_mirroring": False}
    assert capture[0].checkpoint == "checkpoint_best.pth"


def test_a_reload_in_a_cold_process_rebuilds_the_same_predictor(capture, monkeypatch):
    handle = runtime.create_predictor(MODEL, step_size=0.75, tta=False,
                                      checkpoint="checkpoint_best.pth")
    capture.clear()
    monkeypatch.setattr(runtime, "predictor_registered", lambda pid: False)
    monkeypatch.setattr(runtime, "load_predictor", lambda pid: "reloaded")

    runtime._predictor_engine(handle)

    assert capture, "the cold process rebuilt nothing"
    assert capture[0].kwargs == {"tile_step_size": 0.75, "use_mirroring": False}
    assert capture[0].checkpoint == "checkpoint_best.pth"


@pytest.mark.parametrize("bad", [0, -0.5, 1.5])
def test_a_step_size_nnunet_would_refuse_is_refused_here(capture, bad):
    """nnU-Net's own help: cannot be larger than 1. Zero never terminates."""
    with pytest.raises(ValueError) as excinfo:
        runtime.create_predictor(MODEL, step_size=bad)

    assert "step_size" in str(excinfo.value)
