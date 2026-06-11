"""Minimal Vox runtime value model used by the persistent result store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
import math


VOX_FORMAT_VERSION = "voxpod/1"
DEFAULT_PAGE_SIZE = 64
MAX_PAGE_SIZE = 512


class VoxValueError(RuntimeError):
    """Base runtime error for Vox value handling."""


class UnsupportedVoxValueError(VoxValueError):
    """Raised when a runtime value cannot be represented by voxpod/1."""

    code = "E_UNSPECIFIED_VALUE_TYPE"

    def __init__(self, value: Any, message: str | None = None):
        type_name = f"{type(value).__module__}.{type(value).__name__}"
        super().__init__(message or f"Value type '{type_name}' is not supported by voxpod/1.")
        self.value_type = type_name


@dataclass(frozen=True)
class OverlayLayer:
    """One overlay layer with optional viewer metadata."""

    value: Any
    label: str | None = None
    opacity: float | None = None
    colormap: str | None = None
    visible: bool = True


@dataclass(frozen=True)
class OverlayValue:
    """Composite image-like value rendered as layered data."""

    layers: tuple[OverlayLayer, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_layers(cls, layers: Iterable[Any], *, metadata: dict[str, Any] | None = None) -> "OverlayValue":
        return cls(
            layers=tuple(normalize_overlay_layer(layer, index=index) for index, layer in enumerate(layers)),
            metadata=dict(metadata or {}),
        )


def normalize_path(path: str | None) -> str:
    raw = str(path or "").strip()
    if raw in {"", "/"}:
        return ""
    return raw if raw.startswith("/") else f"/{raw}"


def normalize_overlay_layer(layer: Any, *, index: int) -> OverlayLayer:
    raw = layer if isinstance(layer, OverlayLayer) else OverlayLayer(value=layer)
    label = raw.label or ("Base" if index == 0 else f"Overlay {index}")
    colormap = raw.colormap or ("gray" if index == 0 else "red")
    opacity = raw.opacity
    if opacity is not None:
        opacity = max(0.0, min(1.0, float(opacity)))
    return OverlayLayer(
        value=raw.value,
        label=str(label),
        opacity=opacity,
        colormap=str(colormap),
        visible=bool(raw.visible),
    )


def _import_numpy():
    try:
        import numpy as np

        return np
    except Exception:
        return None


def _import_simpleitk():
    try:
        import SimpleITK as sitk

        return sitk
    except Exception:
        return None


def _is_sequence_value(value: Any) -> bool:
    try:
        from voxlogica.execution_strategy.results import SequenceValue

        return isinstance(value, SequenceValue)
    except Exception:
        return False


class VoxValue:
    """Small descriptor adapter for values accepted by this branch."""

    vox_type = "unknown"

    def __init__(self, raw: Any):
        self.raw = raw

    def descriptor_base(self, *, path: str = "", pageable: bool = False, can_descend: bool = False) -> dict[str, Any]:
        return {
            "vox_type": self.vox_type,
            "format_version": VOX_FORMAT_VERSION,
            "summary": {},
            "navigation": {
                "path": normalize_path(path),
                "pageable": bool(pageable),
                "can_descend": bool(can_descend),
                "default_page_size": DEFAULT_PAGE_SIZE,
                "max_page_size": MAX_PAGE_SIZE,
            },
        }

    def describe(self, *, path: str = "") -> dict[str, Any]:
        return self.descriptor_base(path=path)

    def to_json_native(self) -> Any:
        raise UnsupportedVoxValueError(self.raw)


class VoxScalarValue(VoxValue):
    def __init__(self, raw: Any, vox_type: str):
        super().__init__(raw)
        self.vox_type = vox_type

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"value": self.raw}
        if isinstance(self.raw, str):
            payload["summary"] = {"length": len(self.raw), "value": self.raw[:2048], "truncated": len(self.raw) > 2048}
        return payload

    def to_json_native(self) -> Any:
        return self.raw


class VoxBytesValue(VoxValue):
    vox_type = "bytes"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"length": len(self.raw)}
        return payload


class VoxNdArrayValue(VoxValue):
    vox_type = "ndarray"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {
            "dtype": str(self.raw.dtype),
            "shape": [int(v) for v in self.raw.shape],
            "size": int(self.raw.size),
        }
        return payload


class VoxMappingValue(VoxValue):
    vox_type = "mapping"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path, pageable=True, can_descend=True)
        keys = list(self.raw.keys())
        payload["summary"] = {"length": len(keys), "keys_preview": [str(key) for key in keys[:16]], "truncated": len(keys) > 16}
        return payload

    def to_json_native(self) -> Any:
        return {str(key): adapt_runtime_value(value).to_json_native() for key, value in self.raw.items()}


class VoxSequenceValue(VoxValue):
    vox_type = "sequence"

    def _items(self) -> list[Any]:
        if _is_sequence_value(self.raw):
            return list(self.raw.iter_values())
        return list(self.raw)

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path, pageable=True, can_descend=True)
        total_size = getattr(self.raw, "total_size", None)
        length = total_size if total_size is not None else len(self._items())
        payload["summary"] = {"length": int(length)}
        return payload

    def to_json_native(self) -> Any:
        return [adapt_runtime_value(item).to_json_native() for item in self._items()]


class VoxOverlayValue(VoxValue):
    vox_type = "overlay"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path, can_descend=True)
        payload["summary"] = {
            "layer_count": len(self.raw.layers),
            "layer_labels": [str(layer.label) for layer in self.raw.layers],
            "metadata_keys": sorted(str(key) for key in self.raw.metadata),
        }
        return payload


class VoxImageValue(VoxValue):
    vox_type = "image"

    def as_array(self) -> Any:
        sitk = _import_simpleitk()
        if sitk is not None and isinstance(self.raw, sitk.Image):
            return sitk.GetArrayFromImage(self.raw)
        if hasattr(self.raw, "__array__"):
            np = _import_numpy()
            if np is not None:
                return np.asarray(self.raw)
        raise UnsupportedVoxValueError(self.raw)

    def storage_metadata(self) -> dict[str, Any]:
        sitk = _import_simpleitk()
        if sitk is not None and isinstance(self.raw, sitk.Image):
            return {
                "runtime": "simpleitk",
                "spacing": [float(v) for v in self.raw.GetSpacing()],
                "origin": [float(v) for v in self.raw.GetOrigin()],
                "direction": [float(v) for v in self.raw.GetDirection()],
                "components": int(self.raw.GetNumberOfComponentsPerPixel()),
            }
        return {"runtime": "array"}

    def describe(self, *, path: str = "") -> dict[str, Any]:
        array = self.as_array()
        payload = self.descriptor_base(path=path, can_descend=True)
        payload["summary"] = {
            "dtype": str(array.dtype),
            "shape": [int(v) for v in array.shape],
            "size": int(array.size),
            **self.storage_metadata(),
        }
        return payload

    def to_json_native(self) -> Any:
        return self.describe()

def restore_runtime_image(payload_json: dict[str, Any], array: Any) -> Any:
    metadata = dict(payload_json.get("metadata") or {})
    if metadata.get("runtime") != "simpleitk":
        return array

    sitk = _import_simpleitk()
    if sitk is None:
        raise RuntimeError("SimpleITK is required to decode image values.")

    components = int(metadata.get("components") or 1)
    image = sitk.GetImageFromArray(array, isVector=components > 1)
    spacing = metadata.get("spacing")
    origin = metadata.get("origin")
    direction = metadata.get("direction")
    if isinstance(spacing, list):
        image.SetSpacing(tuple(float(v) for v in spacing))
    if isinstance(origin, list):
        image.SetOrigin(tuple(float(v) for v in origin))
    if isinstance(direction, list):
        image.SetDirection(tuple(float(v) for v in direction))
    return image


def adapt_runtime_value(value: Any) -> VoxValue:
    np = _import_numpy()
    sitk = _import_simpleitk()
    if value is None:
        return VoxScalarValue(value, "null")
    if isinstance(value, bool):
        return VoxScalarValue(value, "boolean")
    if isinstance(value, int) and not isinstance(value, bool):
        return VoxScalarValue(value, "integer")
    if isinstance(value, float) and math.isfinite(value):
        return VoxScalarValue(value, "number")
    if isinstance(value, str):
        return VoxScalarValue(value, "string")
    if isinstance(value, bytes):
        return VoxBytesValue(value)
    if np is not None and isinstance(value, np.ndarray):
        return VoxNdArrayValue(value)
    if isinstance(value, OverlayValue):
        return VoxOverlayValue(value)
    if isinstance(value, dict):
        return VoxMappingValue(value)
    if _is_sequence_value(value) or isinstance(value, (list, tuple, range)):
        return VoxSequenceValue(value)
    if sitk is not None and isinstance(value, sitk.Image):
        #print(value)
        return VoxImageValue(value)
    # print(value)
    raise UnsupportedVoxValueError(value)
