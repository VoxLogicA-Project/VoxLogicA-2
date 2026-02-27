"""Canonical voxpod/1 serialization for persisted results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import base64
import json
import math

from voxlogica.value_model import (
    VOX_FORMAT_VERSION,
    VoxDaskBagSequenceValue,
    VoxImageValue,
    VoxIteratorSequenceValue,
    VoxMappingValue,
    VoxNdArrayValue,
    VoxPythonSequenceValue,
    VoxSequenceValue,
    UnsupportedVoxValueError,
    adapt_runtime_value,
)


@dataclass(frozen=True)
class EncodedPage:
    """Persisted page payload for pageable values."""

    path: str
    offset: int
    limit: int
    descriptor: dict[str, Any]
    payload_json: dict[str, Any]
    payload_bin: bytes | None = None


@dataclass(frozen=True)
class EncodedRecord:
    """Persisted root payload for a node."""

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


def _import_simpleitk():
    try:
        import SimpleITK as sitk

        return sitk
    except Exception:
        return None


def _is_json_native(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return True
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


def _image_payload(image_value: VoxImageValue) -> tuple[str, dict[str, Any], bytes]:
    np = _import_numpy()
    sitk = _import_simpleitk()
    if np is None or sitk is None:
        raise UnsupportedVoxValueError(image_value.raw, "SimpleITK image serialization requires NumPy and SimpleITK.")
    arr = sitk.GetArrayFromImage(image_value.raw)
    if arr.ndim == 2:
        vox_type = "image2d"
    elif arr.ndim == 3:
        vox_type = "volume3d"
    else:
        raise UnsupportedVoxValueError(image_value.raw, f"Unsupported image dimension {arr.ndim}.")
    meta = image_value.describe(path="")["summary"]
    header, payload_bin = _ndarray_payload(arr)
    return vox_type, {
        "encoding": "sitk-image-binary-v1",
        "array": header,
        "image_meta": {
            "dimension": int(meta["dimension"]),
            "size": [int(v) for v in meta["size"]],
            "spacing": [float(v) for v in meta["spacing"]],
            "origin": [float(v) for v in meta["origin"]],
            "direction": [float(v) for v in meta["direction"]],
            "pixel_id": str(meta["pixel_id"]),
        },
    }, payload_bin


def _encode_sequence_pages(sequence: VoxSequenceValue, *, page_size: int) -> tuple[dict[str, Any], list[EncodedPage]]:
    def _encode_embedded_item(raw_value: Any, descriptor: dict[str, Any]) -> dict[str, Any]:
        encoded = encode_for_storage(raw_value, page_size=page_size)
        if encoded.pages:
            raise UnsupportedVoxValueError(
                descriptor,
                "Nested pageable sequence values are not supported in sequence page items.",
            )
        embedded: dict[str, Any] = {
            "encoding": "embedded-voxpod-v1",
            "format_version": encoded.format_version,
            "vox_type": encoded.vox_type,
            "descriptor": encoded.descriptor,
            "payload_json": encoded.payload_json,
        }
        if encoded.payload_bin is not None:
            embedded["payload_bin_b64"] = base64.b64encode(encoded.payload_bin).decode("ascii")
        return {"__vox_page_item__": embedded}

    pages: list[EncodedPage] = []
    offset = 0
    total = 0
    has_more = True
    while has_more:
        page = sequence.page(offset=offset, limit=page_size)
        raw_items: list[Any] = []
        for index, item in enumerate(page.items):
            descriptor = item.get("descriptor", {})
            if "value" in item:
                value = item.get("value")
                if not _is_json_native(value):
                    raise UnsupportedVoxValueError(
                        descriptor,
                        f"Sequence item {offset + index} is not persistable as JSON-native voxpod data.",
                    )
                raw_items.append(value)
                continue
            if "_raw" not in item:
                raise UnsupportedVoxValueError(
                    descriptor,
                    f"Sequence item {offset + index} is missing persistence payload.",
                )
            raw_items.append(_encode_embedded_item(item["_raw"], descriptor))
        has_more = bool(page.has_more)
        pages.append(
            EncodedPage(
                path="",
                offset=offset,
                limit=page.limit,
                descriptor={
                    "vox_type": "sequence-page",
                    "format_version": VOX_FORMAT_VERSION,
                    "summary": {"offset": offset, "limit": page.limit, "count": len(raw_items)},
                    "navigation": {
                        "path": "",
                        "pageable": False,
                        "can_descend": False,
                        "default_page_size": page_size,
                        "max_page_size": page_size,
                    },
                },
                payload_json={"items": raw_items, "has_more": has_more},
            )
        )
        offset += len(raw_items)
        total += len(raw_items)
        if len(raw_items) == 0:
            break

    summary = {"encoding": "sequence-pages-v1", "length": total, "page_size": page_size}
    return summary, pages


def _mapping_to_json(mapping: VoxMappingValue) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.raw.items():
        if not isinstance(key, str):
            raise UnsupportedVoxValueError(value, "Mapping keys must be strings under voxpod/1.")
        adapted = adapt_runtime_value(value)
        json_value = adapted.to_json_native()
        out[key] = _json_native_or_raise(json_value, context=f"Mapping value '{key}'")
    return out


def can_serialize_value(value: Any) -> tuple[bool, str | None]:
    """Fast capability check for write-through persistence."""
    try:
        adapt_runtime_value(value)
    except UnsupportedVoxValueError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, None


def encode_for_storage(value: Any, *, page_size: int = 128) -> EncodedRecord:
    """Encode runtime value into canonical voxpod/1 record."""
    adapted = adapt_runtime_value(value)
    descriptor = adapted.describe(path="")
    vox_type = str(descriptor.get("vox_type", adapted.vox_type))

    if vox_type in {"null", "boolean", "integer", "number", "string"}:
        summary = descriptor.get("summary", {})
        payload_json = {"encoding": "scalar-json-v1", "value": summary.get("value")}
        _json_native_or_raise(payload_json["value"], context=f"{vox_type} scalar value")
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type=vox_type,
            descriptor=descriptor,
            payload_json=payload_json,
        )

    if vox_type == "bytes":
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type=vox_type,
            descriptor=descriptor,
            payload_json={"encoding": "bytes-binary-v1", "length": int(len(adapted.raw))},
            payload_bin=bytes(adapted.raw),
        )

    if isinstance(adapted, VoxNdArrayValue):
        payload_json, payload_bin = _ndarray_payload(adapted.raw)
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type=vox_type,
            descriptor=descriptor,
            payload_json=payload_json,
            payload_bin=payload_bin,
        )

    if isinstance(adapted, VoxImageValue):
        image_vox_type, payload_json, payload_bin = _image_payload(adapted)
        descriptor = dict(descriptor)
        descriptor["vox_type"] = image_vox_type
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type=image_vox_type,
            descriptor=descriptor,
            payload_json=payload_json,
            payload_bin=payload_bin,
        )

    if isinstance(adapted, VoxMappingValue):
        payload_value = _mapping_to_json(adapted)
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type="mapping",
            descriptor=descriptor,
            payload_json={"encoding": "mapping-json-v1", "value": payload_value},
        )

    if isinstance(adapted, (VoxPythonSequenceValue, VoxIteratorSequenceValue, VoxDaskBagSequenceValue)):
        sequence_meta, pages = _encode_sequence_pages(adapted, page_size=page_size)
        descriptor = dict(descriptor)
        summary = dict(descriptor.get("summary", {}))
        summary["length"] = sequence_meta["length"]
        summary["page_size"] = sequence_meta["page_size"]
        descriptor["summary"] = summary
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type="sequence",
            descriptor=descriptor,
            payload_json=sequence_meta,
            pages=pages,
        )

    raise UnsupportedVoxValueError(value)


def decode_runtime_value(vox_type: str, payload_json: dict[str, Any], payload_bin: bytes | None) -> Any:
    """Decode one persisted payload to a runtime value."""
    np = _import_numpy()
    sitk = _import_simpleitk()
    encoding = str(payload_json.get("encoding", ""))

    if vox_type in {"null", "boolean", "integer", "number", "string"}:
        return payload_json.get("value")
    if vox_type == "bytes":
        return bytes(payload_bin or b"")
    if vox_type == "mapping":
        value = payload_json.get("value")
        if isinstance(value, dict):
            return value
        raise ValueError("Invalid mapping payload.")
    if vox_type == "sequence":
        return payload_json
    if vox_type == "ndarray":
        if np is None:
            raise RuntimeError("NumPy is required to decode ndarray values.")
        if encoding != "ndarray-binary-v1":
            raise ValueError(f"Unsupported ndarray encoding: {encoding}")
        dtype = np.dtype(str(payload_json["dtype"]))
        shape = tuple(int(v) for v in payload_json["shape"])
        if payload_bin is None:
            raise ValueError("Missing binary payload for ndarray value.")
        flat = np.frombuffer(payload_bin, dtype=dtype)
        return flat.reshape(shape, order="C")
    if vox_type in {"image2d", "volume3d"}:
        if np is None or sitk is None:
            raise RuntimeError("SimpleITK and NumPy are required to decode image values.")
        if encoding != "sitk-image-binary-v1":
            raise ValueError(f"Unsupported image encoding: {encoding}")
        array_header = payload_json.get("array")
        image_meta = payload_json.get("image_meta")
        if not isinstance(array_header, dict) or not isinstance(image_meta, dict):
            raise ValueError("Malformed image payload header.")
        arr = decode_runtime_value("ndarray", array_header, payload_bin)
        image = sitk.GetImageFromArray(arr)
        image.SetSpacing(tuple(float(v) for v in image_meta.get("spacing", (1.0, 1.0, 1.0))))
        image.SetOrigin(tuple(float(v) for v in image_meta.get("origin", (0.0, 0.0, 0.0))))
        direction = image_meta.get("direction")
        if isinstance(direction, list):
            image.SetDirection(tuple(float(v) for v in direction))
        return image
    raise ValueError(f"Unsupported vox_type '{vox_type}' for runtime decode.")


def decode_page_payload(payload_json: dict[str, Any]) -> dict[str, Any]:
    """Decode persisted page payload to canonical API page data."""
    items = payload_json.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Invalid page payload: 'items' must be a list.")
    return {
        "items": items,
        "has_more": bool(payload_json.get("has_more", False)),
    }


def dumps_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def loads_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return parsed
    raise ValueError("Expected JSON object payload.")
