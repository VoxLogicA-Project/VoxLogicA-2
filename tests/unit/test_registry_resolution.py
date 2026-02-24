from __future__ import annotations

from collections import OrderedDict

import pytest

from voxlogica.lazy.ir import NodeSpec
from voxlogica.primitives.api import AritySpec, PrimitiveCall, PrimitiveSpec
from voxlogica.primitives.registry import PrimitiveRegistry


def _dummy_planner(name: str):
    def _planner(call: PrimitiveCall) -> NodeSpec:
        return NodeSpec(
            kind="primitive",
            operator=name,
            args=call.args,
            kwargs=call.kwargs,
            attrs=call.attrs,
            output_kind="scalar",
        )

    return _planner


def _make_spec(namespace: str, name: str, kernel_name: str) -> PrimitiveSpec:
    return PrimitiveSpec(
        name=name,
        namespace=namespace,
        kind="scalar",
        arity=AritySpec.fixed(0),
        attrs_schema={},
        planner=_dummy_planner(kernel_name),
        kernel_name=kernel_name,
        description=f"{namespace}.{name}",
    )


@pytest.mark.unit
def test_unqualified_resolution_is_deterministic(monkeypatch):
    registry = PrimitiveRegistry()

    # Inject synthetic overlapping names to verify import-order semantics.
    spec_a = _make_spec("ns_a", "foo", "ns_a.foo")
    spec_b = _make_spec("ns_b", "foo", "ns_b.foo")

    registry.register(spec_a, lambda: "a")
    registry.register(spec_b, lambda: "b")

    registry._loaded_namespaces.update({"ns_a", "ns_b"})
    registry._specs_by_namespace.setdefault("ns_a", OrderedDict())["foo"] = spec_a
    registry._specs_by_namespace.setdefault("ns_b", OrderedDict())["foo"] = spec_b

    registry._import_order = ["default", "ns_b", "ns_a"]

    resolved = [registry.resolve("foo").qualified_name for _ in range(20)]
    assert resolved == ["ns_b.foo"] * 20


@pytest.mark.unit
def test_qualified_resolution_is_exact():
    registry = PrimitiveRegistry()
    spec = registry.resolve("default.addition")
    assert spec.qualified_name == "default.addition"
