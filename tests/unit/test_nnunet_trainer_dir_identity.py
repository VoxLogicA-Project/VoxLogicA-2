"""The trainer directory must be resolved by identity, not by being the only one.

`trainer_dir` used to glob for ``*__nnUNetPlans__<config>`` and accept a unique
match. That was wrong in both directions:

  - with two trainers in one dataset folder it raised "ambiguous trainer
    directories" and the run died, although the program had named its trainer;
  - with one trainer it returned that one whatever was asked for, so a request
    for a ten-epoch model could be answered with a 250-epoch checkpoint -- and
    train_model would then log "checkpoint already exists", skip training, and
    hand back a model nobody asked for.

Both are silent-substitution bugs, which is why the resolution is pinned here.
"""

from __future__ import annotations

import pytest

from voxlogica.primitives.nnunet.runtime import trainer_dir


CONFIG = "3d_fullres"


def results_with(tmp_path, *trainers: str):
    dataset = tmp_path / "Dataset900_B24Match"
    for name in trainers:
        (dataset / f"{name}__nnUNetPlans__{CONFIG}" / "fold_0").mkdir(parents=True)
    return tmp_path


def test_picks_the_named_trainer_when_several_are_present(tmp_path):
    root = results_with(tmp_path, "nnUNetTrainer_10epochs", "nnUNetTrainer_250epochs")

    chosen = trainer_dir(root, "Dataset900_B24Match", CONFIG, "nnUNetTrainer_10epochs")

    assert chosen.name == f"nnUNetTrainer_10epochs__nnUNetPlans__{CONFIG}"


def test_does_not_substitute_another_trainer_when_the_named_one_is_absent(tmp_path):
    root = results_with(tmp_path, "nnUNetTrainer_250epochs")

    with pytest.raises(ValueError) as excinfo:
        trainer_dir(root, "Dataset900_B24Match", CONFIG, "nnUNetTrainer_10epochs")

    message = str(excinfo.value)
    assert "nnUNetTrainer_10epochs" in message     # what was asked for
    assert "nnUNetTrainer_250epochs" in message    # and what is actually there


def test_without_a_named_trainer_a_unique_match_is_still_accepted(tmp_path):
    root = results_with(tmp_path, "nnUNetTrainer_250epochs")

    chosen = trainer_dir(root, "Dataset900_B24Match", CONFIG)

    assert chosen.name == f"nnUNetTrainer_250epochs__nnUNetPlans__{CONFIG}"


def test_without_a_named_trainer_two_matches_are_still_ambiguous(tmp_path):
    root = results_with(tmp_path, "nnUNetTrainer_10epochs", "nnUNetTrainer_250epochs")

    with pytest.raises(ValueError, match="ambiguous"):
        trainer_dir(root, "Dataset900_B24Match", CONFIG)


def test_a_different_configuration_is_not_a_match(tmp_path):
    root = results_with(tmp_path, "nnUNetTrainer_10epochs")

    with pytest.raises(ValueError):
        trainer_dir(root, "Dataset900_B24Match", "2d", "nnUNetTrainer_10epochs")
