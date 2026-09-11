"""`strings.split` and `strings.equals`: enough string to read a CSV a dataset ships.

Added so a program can select BraTS cases by the grade in name_mapping.csv
rather than by where they sort in a directory. Two kernels, one filter.
"""

from __future__ import annotations

import pytest

from voxlogica.primitives.strings.equals import execute as equals
from voxlogica.primitives.strings.split import execute as split


@pytest.mark.unit
def test_split_defaults_to_comma_and_keeps_empty_fields() -> None:
    assert split(**{"0": "HGG,a,,d"}) == ["HGG", "a", "", "d"]
    assert split(**{"0": "a;b", "1": ";"}) == ["a", "b"]


@pytest.mark.unit
def test_equals_is_string_equality_after_str() -> None:
    assert equals(**{"0": "HGG", "1": "HGG"}) is True
    assert equals(**{"0": "HGG", "1": "LGG"}) is False
    assert equals(**{"0": 1, "1": "1"}) is True   # both sides go through str()


@pytest.mark.unit
def test_the_name_mapping_row_shape() -> None:
    """The exact row shape the nnU-Net program reads: grade first, 2020 id last."""
    row = "HGG,Brats17_CBICA_AAB_1,Brats18_CBICA_AAB_1,NA,BraTS19_CBICA_AAB_1,BraTS20_Training_001"
    fields = split(**{"0": row})
    assert equals(**{"0": fields[0], "1": "HGG"})
    assert fields[5] == "BraTS20_Training_001"
    header = "Grade,BraTS_2017_subject_ID,BraTS_2018_subject_ID,TCGA_TCIA_subject_ID,BraTS_2019_subject_ID,BraTS_2020_subject_ID"
    assert not equals(**{"0": split(**{"0": header})[0], "1": "HGG"})  # the header row is filtered out
