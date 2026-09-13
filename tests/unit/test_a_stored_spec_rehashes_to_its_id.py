"""A spec read back from the store must re-hash to the id it was stored under.

THIS IS THE CHECK EVERYTHING ELSE RESTS ON. Reusing a stored expansion is only
safe because identity is verifiable: a node's id IS the hash of its spec, so a
spec that does not hash to its own id is not that spec and must be discarded
rather than trusted. Without this property the engine can only ever re-derive
what it already has.

It did not hold. `hash_node` digests six things -- kind, operator, args, kwargs,
attrs and **output_kind** -- and the `node` table stored five. Measured against a
real sixty-case store: **30 of 5,000 rows re-hashed to their own id**, and the
4,970 failures were ordinary attribute-free primitives, not exotic nodes.
"""

from __future__ import annotations

import json

import pytest

from voxlogica.lazy.hash import hash_node
from voxlogica.lazy.ir import NodeSpec
from voxlogica.storage import spec_row_for


def _restore(row) -> NodeSpec:
    """Rebuild a spec from its stored row, the way a warm run would."""
    _hash, kind, operator, args, kwargs, attrs, output_kind = row
    return NodeSpec(
        kind=kind,
        operator=operator,
        args=tuple(args[i:i + 32].hex() for i in range(0, len(args), 32)),
        kwargs=tuple(sorted((k, v) for k, v in (json.loads(kwargs).items()
                                                if kwargs else []))),
        attrs=json.loads(attrs) if attrs else {},
        output_kind=output_kind,
    )


_SPECS = [
    NodeSpec(kind="primitive", operator="vox1.dt2", args=("a" * 64,)),
    NodeSpec(kind="primitive", operator="vox1.geq_sv", args=("a" * 64, "b" * 64)),
    NodeSpec(kind="primitive", operator="vox1.mask", args=("c" * 64,),
             output_kind="overlay"),
    NodeSpec(kind="primitive", operator="default.index", args=("d" * 64,),
             output_kind="sequence"),
    NodeSpec(kind="primitive", operator="vox1.volume", args=("e" * 64,),
             kwargs=(("axis", "f" * 64),), output_kind="scalar"),
]


@pytest.mark.unit
@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.operator)
def test_a_stored_spec_rehashes_to_its_id(spec: NodeSpec) -> None:
    node_id = hash_node(spec)
    restored = _restore(spec_row_for(node_id, spec))
    assert hash_node(restored) == node_id, (
        f"{spec.operator} does not survive the round trip: stored under "
        f"{node_id[:12]}, reads back as {hash_node(restored)[:12]}")


@pytest.mark.unit
def test_output_kind_is_part_of_the_identity() -> None:
    """The field whose absence broke it: two specs differing only there are
    different nodes, and a store that drops it cannot tell them apart."""
    a = NodeSpec(kind="primitive", operator="vox1.dt2", args=("a" * 64,),
                 output_kind="scalar")
    b = NodeSpec(kind="primitive", operator="vox1.dt2", args=("a" * 64,),
                 output_kind="overlay")
    assert hash_node(a) != hash_node(b)
    assert spec_row_for(hash_node(a), a)[6] == "scalar"
    assert spec_row_for(hash_node(b), b)[6] == "overlay"
