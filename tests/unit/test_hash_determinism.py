from __future__ import annotations

import pytest

from voxlogica.lazy.hash import hash_node
from voxlogica.lazy.ir import NodeSpec


@pytest.mark.unit
def test_hash_determinism_for_identical_nodes():
    node = NodeSpec(
        kind="primitive",
        operator="default.addition",
        args=("a", "b"),
        kwargs=(("scale", "c"),),
        attrs={"flag": True},
        output_kind="scalar",
    )

    assert hash_node(node) == hash_node(node)


@pytest.mark.unit
def test_hash_normalizes_kwarg_order():
    node_a = NodeSpec(
        kind="primitive",
        operator="default.test",
        args=("x",),
        kwargs=(("b", "2"), ("a", "1")),
        attrs={},
        output_kind="scalar",
    )
    node_b = NodeSpec(
        kind="primitive",
        operator="default.test",
        args=("x",),
        kwargs=(("a", "1"), ("b", "2")),
        attrs={},
        output_kind="scalar",
    )

    assert hash_node(node_a) == hash_node(node_b)
