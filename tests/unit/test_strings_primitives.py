from __future__ import annotations

import pytest

from voxlogica.primitives.strings.concat import execute as concat
from voxlogica.primitives.strings.format_string import execute as format_string


@pytest.mark.unit
def test_concat_primitive():
    assert concat(**{"0": "a", "1": "_", "2": 7}) == "a_7"
    with pytest.raises(ValueError):
        concat()


@pytest.mark.unit
def test_format_string_primitive():
    assert format_string(**{"0": "x_{:03.0f}", "1": 7}) == "x_007"
    assert format_string(**{"0": "{}-{}", "1": "a", "2": "b"}) == "a-b"
    with pytest.raises(ValueError):
        format_string()
    with pytest.raises(ValueError):
        format_string(**{"0": "{:d}", "1": "not-int"})

