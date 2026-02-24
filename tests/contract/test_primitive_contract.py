from __future__ import annotations

import warnings

import pytest

from voxlogica.lazy.ir import NodeSpec
from voxlogica.primitives.api import PrimitiveCall
from voxlogica.primitives.registry import PrimitiveRegistry


@pytest.mark.contract
def test_primitive_specs_have_required_contract_fields():
    registry = PrimitiveRegistry()

    for name in [
        "default.addition",
        "default.range",
        "default.map",
        "default.for_loop",
        "default.load",
        "default.print_primitive",
    ]:
        spec = registry.resolve(name)
        assert spec.name
        assert spec.namespace
        assert spec.kernel_name
        assert spec.kind in {"scalar", "sequence", "tree", "dataset", "effect"}

        planned = spec.planner(PrimitiveCall(args=(), kwargs=(), attrs={}))
        assert isinstance(planned, NodeSpec)


@pytest.mark.contract
def test_legacy_adapter_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        PrimitiveRegistry()

    assert any(
        "Legacy primitive contract" in str(item.message)
        for item in caught
    )
