"""Canonical voxpod/1 serialization used by the result store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import base64
import json
import math

from voxlogica.value_model import (
    OverlayLayer,
    OverlayValue,
    UnsupportedVoxValueError,
    VOX_FORMAT_VERSION,
    VoxBytesValue,
    VoxMappingValue,
    VoxNdArrayValue,
    VoxOverlayValue,
    VoxSequenceValue,
    adapt_runtime_value,
    normalize_overlay_layer,
)


@dataclass(frozen=True)
class EncodedPage:
    path: str
    offset: int
    limit: int
    descriptor: dict[str, Any]
    payload_json: dict[str, Any]
    payload_bin: bytes | None = None


@dataclass(frozen=True)
class EncodedRecord:
    format_version: str
    vox_type: str
    descriptor: dict[str, Any]
    payload_json: dict[str, Any]
    payload_bin: bytes | None = None
    pages: list[EncodedPage] = field(default_factory=list)


def _import_numpy():
    try:
        import numpy as np

        return np
    except Exception:
        return None


def _is_json_native(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_native(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_native(item) for key, item in value.items())
    return False


def _json_native_or_raise(value: Any, *, context: str) -> Any:
    if not _is_json_native(value):
        raise UnsupportedVoxValueError(value, f"{context} is not JSON-native under voxpod/1.")
    return value


def _ndarray_payload(array: Any) -> tuple[dict[str, Any], bytes]:
    return {
        "encoding": "ndarray-binary-v1",
        "dtype": str(array.dtype),
        "shape": [int(v) for v in array.shape],
        "order": "C",
        "byte_order": "little",
    }, bytes(array.tobytes(order="C"))


def _encode_embedded_record(value: Any, *, page_size: int) -> dict[str, Any]:
    encoded = encode_for_storage(value, page_size=page_size)
    payload: dict[str, Any] = {
        "encoding": "embedded-voxpod-v1",
        "format_version": encoded.format_version,
        "vox_type": encoded.vox_type,
        "descriptor": encoded.descriptor,
        "payload_json": encoded.payload_json,
    }
    if encoded.payload_bin is not None:
        payload["payload_bin_b64"] = base64.b64encode(encoded.payload_bin).decode("ascii")
    return payload


def _decode_embedded_record(payload: dict[str, Any]) -> Any:
    payload_bin = None
    encoded_bin = payload.get("payload_bin_b64")
    if isinstance(encoded_bin, str):
        payload_bin = base64.b64decode(encoded_bin)
    return decode_runtime_value(str(payload["vox_type"]), dict(payload["payload_json"]), payload_bin)


def can_serialize_value(value: Any) -> tuple[bool, str | None, EncodedRecord | None]:
    try:
        record = encode_for_storage(value)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), None
    return True, None, record


def encode_for_storage(value: Any, *, page_size: int = 128) -> EncodedRecord:
    adapted = adapt_runtime_value(value)
    descriptor = adapted.describe(path="")
    vox_type = str(descriptor.get("vox_type", adapted.vox_type))

    if vox_type in {"null", "boolean", "integer", "number", "string"}:
        payload_value = descriptor.get("summary", {}).get("value")
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type=vox_type,
            descriptor=descriptor,
            payload_json={"encoding": "scalar-json-v1", "value": _json_native_or_raise(payload_value, context=vox_type)},
        )

    if isinstance(adapted, VoxBytesValue):
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type="bytes",
            descriptor=descriptor,
            payload_json={"encoding": "bytes-binary-v1", "length": len(value)},
            payload_bin=bytes(value),
        )

    if isinstance(adapted, VoxNdArrayValue):
        payload_json, payload_bin = _ndarray_payload(adapted.raw)
        return EncodedRecord(VOX_FORMAT_VERSION, "ndarray", descriptor, payload_json, payload_bin)

    if isinstance(adapted, VoxMappingValue):
        return EncodedRecord(
            VOX_FORMAT_VERSION,
            "mapping",
            descriptor,
            {"encoding": "mapping-json-v1", "value": adapted.to_json_native()},
        )

    if isinstance(adapted, VoxSequenceValue):
        items = adapted.to_json_native()
        return EncodedRecord(
            VOX_FORMAT_VERSION,
            "sequence",
            descriptor,
            {"encoding": "sequence-json-v1", "value": items, "length": len(items)},
        )

    if isinstance(adapted, VoxOverlayValue):
        layers = []
        for index, raw_layer in enumerate(adapted.raw.layers):
            layer = normalize_overlay_layer(raw_layer, index=index)
            layers.append(
                {
                    "label": layer.label,
                    "visible": layer.visible,
                    "opacity": layer.opacity,
                    "colormap": layer.colormap,
                    "value": _encode_embedded_record(layer.value, page_size=page_size),
                }
            )
        return EncodedRecord(
            VOX_FORMAT_VERSION,
            "overlay",
            descriptor,
            {"encoding": "overlay-v1", "layers": layers, "metadata": dict(adapted.raw.metadata)},
        )

    raise UnsupportedVoxValueError(value)


def decode_runtime_value(vox_type: str, payload_json: dict[str, Any], payload_bin: bytes | None) -> Any:
    np = _import_numpy()
    if vox_type in {"null", "boolean", "integer", "number", "string"}:
        return payload_json.get("value")
    if vox_type == "bytes":
        return bytes(payload_bin or b"")
    if vox_type == "mapping":
        return dict(payload_json.get("value") or {})
    if vox_type == "sequence":
        return list(payload_json.get("value") or [])
    if vox_type == "ndarray":
        if np is None:
            raise RuntimeError("NumPy is required to decode ndarray values.")
        dtype = np.dtype(str(payload_json["dtype"]))
        shape = tuple(int(v) for v in payload_json["shape"])
        return np.frombuffer(payload_bin or b"", dtype=dtype).reshape(shape, order="C")
    if vox_type == "overlay":
        layers = []
        for index, raw_layer in enumerate(payload_json.get("layers") or []):
            raw_value = raw_layer.get("value")
            if not isinstance(raw_value, dict):
                raise ValueError(f"Overlay layer {index} is missing embedded value.")
            layers.append(
                OverlayLayer(
                    value=_decode_embedded_record(raw_value),
                    label=raw_layer.get("label"),
                    opacity=raw_layer.get("opacity"),
                    colormap=raw_layer.get("colormap"),
                    visible=bool(raw_layer.get("visible", True)),
                )
            )
        return OverlayValue(layers=tuple(layers), metadata=dict(payload_json.get("metadata") or {}))
    raise ValueError(f"Unsupported vox_type '{vox_type}' for runtime decode.")


def decode_page_payload(payload_json: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(payload_json.get("items") or []), "has_more": bool(payload_json.get("has_more", False))}


def dumps_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def loads_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object payload.")
    return parsed
