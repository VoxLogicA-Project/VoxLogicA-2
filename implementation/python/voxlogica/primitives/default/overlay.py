"""Overlay primitive for layered image/volume visualization."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.value_model import (
    OverlayLayer,
    OverlayValue,
    UnsupportedVoxValueError,
    adapt_runtime_value,
)


def _is_dask_bag(value: Any) -> bool:
    try:
        import dask.bag as db  # type: ignore

        return isinstance(value, db.Bag)
    except Exception:
        return False


def _coerce_overlay_layer(value: Any, *, index: int) -> OverlayLayer:
    if isinstance(value, OverlayLayer):
        return value
    if isinstance(value, dict) and "value" in value:
        opacity = value.get("opacity")
        return OverlayLayer(
            value=value["value"],
            label=str(value.get("label") or ""),
            opacity=float(opacity) if opacity is not None else None,
            colormap=str(value["colormap"]) if value.get("colormap") else None,
            visible=bool(value.get("visible", True)),
        )
    return OverlayLayer(value=value)


def _layers_from_single_arg(source: Any) -> tuple[list[OverlayLayer], dict[str, Any]] | None:
    metadata: dict[str, Any] = {}
    layers_source = source

    if isinstance(source, OverlayValue):
        return list(source.layers), dict(source.metadata)

    if isinstance(source, dict) and "layers" in source:
        layers_source = source.get("layers", [])
        raw_metadata = source.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata.update({str(key): value for key, value in raw_metadata.items()})
        for key, value in source.items():
            if key in {"layers", "metadata"}:
                continue
            metadata[str(key)] = value

    try:
        adapted = adapt_runtime_value(layers_source)
        if adapted.vox_type in {"ndarray", "image2d", "volume3d", "overlay"}:
            return None
    except UnsupportedVoxValueError:
        pass

    if isinstance(layers_source, SequenceValue):
        return [_coerce_overlay_layer(item, index=index) for index, item in enumerate(layers_source.iter_values())], metadata
    if _is_dask_bag(layers_source):
        return [_coerce_overlay_layer(item, index=index) for index, item in enumerate(layers_source.compute())], metadata
    if isinstance(layers_source, (list, tuple)):
        return [_coerce_overlay_layer(item, index=index) for index, item in enumerate(layers_source)], metadata
    if isinstance(layers_source, Iterable) and not isinstance(layers_source, (str, bytes, dict)):
        return [_coerce_overlay_layer(item, index=index) for index, item in enumerate(layers_source)], metadata
    return None


def execute(**kwargs) -> OverlayValue:
    """Build an overlay value from layers or one layer collection."""
    ordered_args = [kwargs[str(index)] for index in range(len(kwargs)) if str(index) in kwargs]
    if not ordered_args:
        raise ValueError("overlay requires at least one image-like argument")

    if len(ordered_args) == 1:
        coerced = _layers_from_single_arg(ordered_args[0])
        if coerced is not None:
            layers, metadata = coerced
            if not layers:
                raise ValueError("overlay requires at least one layer")
            return OverlayValue.from_layers(layers, metadata=metadata)

    layers = [_coerce_overlay_layer(arg, index=index) for index, arg in enumerate(ordered_args)]
    return OverlayValue.from_layers(layers, metadata={})


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="overlay",
    namespace="default",
    kind="overlay",
    arity=AritySpec.variadic(1),
    attrs_schema={},
    planner=default_planner_factory("default.overlay", kind="overlay"),
    kernel_name="default.overlay",
    description="Create a layered overlay value from one or more images/volumes",
)
