"""The belt behind the brace: the state writer must encode a foreign scalar.

`runtime._plain` normalises nnU-Net's postprocessing decision where the pickle
is read, and `test_nnunet_decision_is_json` covers that thoroughly. This file
covers only what that one does not: the LAST line of defence, `save_state`,
which serialises the work root's state file.

Why both exist. On 2026-09-18 a 1000-epoch fold finished -- mean validation
Dice 0.9353, `checkpoint_final.pth` written -- and the run then died with
`Object of type int64 is not JSON serializable`, reported as "nnUNet training
failed", which is exactly what it was not. The fix for that decision had
already landed on main ten days earlier and was not yet merged here. The brace
is the right place to convert; this is the belt, so that the NEXT field to
arrive from a third-party artefact cannot cost a run the same way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.primitives.nnunet.materialize import load_state, save_state


@pytest.mark.unit
def test_the_state_file_round_trips_a_foreign_scalar(tmp_path: Path) -> None:
    numpy = pytest.importorskip("numpy")
    save_state(tmp_path, {"dataset_id": 900,
                          "postprocessing": {"kwargs": [{"label": numpy.int64(7)}]}})
    state = load_state(tmp_path)
    assert state is not None
    assert state["postprocessing"]["kwargs"][0]["label"] == 7


@pytest.mark.unit
def test_an_ordinary_state_is_written_unchanged(tmp_path: Path) -> None:
    """A writer that rewrites what it should leave alone is its own bug."""
    payload = {"dataset_id": 900, "modalities": ["flair", "t1"],
               "trained_folds": [0], "postprocessing": None}
    save_state(tmp_path, payload)
    assert load_state(tmp_path) == payload
