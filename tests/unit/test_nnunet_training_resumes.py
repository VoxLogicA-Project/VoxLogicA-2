"""An interrupted training must continue, not start again at epoch zero.

nnU-Net writes `checkpoint_latest.pth` every 50 epochs and accepts `--c` to
continue from it. The orchestration never passed that flag, so a training that
died part-way silently restarted from scratch: on the 1000-epoch schedule this
dataset needs (~35 s/epoch), a machine hiccup at hour nine cost all nine hours.
"""

from __future__ import annotations

from voxlogica.primitives.nnunet.runtime import fold_complete, fold_resumable


def fold_dir(tmp_path, *checkpoints: str):
    directory = tmp_path / "fold_0"
    directory.mkdir(parents=True, exist_ok=True)
    for name in checkpoints:
        (directory / name).write_bytes(b"")
    return tmp_path


def test_a_half_finished_fold_is_resumable(tmp_path):
    trainer = fold_dir(tmp_path, "checkpoint_latest.pth")

    assert fold_resumable(trainer, 0) is True
    assert fold_complete(trainer, 0) is False


def test_a_finished_fold_is_not_resumed(tmp_path):
    # Both files exist at the end of a normal training: complete wins, or the
    # engine would re-run a training it already has.
    trainer = fold_dir(tmp_path, "checkpoint_latest.pth", "checkpoint_final.pth")

    assert fold_complete(trainer, 0) is True
    assert fold_resumable(trainer, 0) is False


def test_a_fold_that_never_started_is_not_resumable(tmp_path):
    trainer = fold_dir(tmp_path)

    assert fold_resumable(trainer, 0) is False
    assert fold_complete(trainer, 0) is False


def test_only_a_best_checkpoint_is_not_enough(tmp_path):
    # checkpoint_best.pth appears early and often; it is not what --c reads.
    trainer = fold_dir(tmp_path, "checkpoint_best.pth")

    assert fold_resumable(trainer, 0) is False
