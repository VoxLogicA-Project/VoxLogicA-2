from __future__ import annotations

import pytest
pytest.importorskip("hypothesis")
from hypothesis import given, strategies as st

from voxlogica.lazy.hash import hash_node
from voxlogica.lazy.ir import NodeSpec


@pytest.mark.unit
@given(st.dictionaries(st.text(min_size=1, max_size=4), st.integers(min_value=0, max_value=100), max_size=4))
def test_hash_is_stable_for_same_attrs(attrs):
    node = NodeSpec(
        kind="primitive",
        operator="default.test",
        args=("a",),
        kwargs=(),
        attrs=attrs,
        output_kind="scalar",
    )
    assert hash_node(node) == hash_node(node)
