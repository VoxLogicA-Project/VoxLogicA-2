from __future__ import annotations

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
def test_all_registered_primitives_use_stable_contract():
    registry = PrimitiveRegistry()
    for namespace in registry.list_namespaces():
        registry.import_namespace(namespace)

    specs = [registry.get_spec(name) for name in registry.list_primitives().keys()]
    assert specs, "Expected at least one registered primitive"
    assert not any(spec.is_legacy_adapter for spec in specs)
