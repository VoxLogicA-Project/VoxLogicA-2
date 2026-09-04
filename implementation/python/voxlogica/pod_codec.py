"""Canonical voxpod/1 serialization used by the result store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import base64
import gzip
import json
import math

# Binary payloads (voxel buffers, ndarrays) are compressed before storage --
# label maps and threshold masks are mostly-constant and shrink losslessly by a
# large factor. Compression runs on the async persister thread, so its speed is
# the persistence bottleneck: measured on a real 35.7 MB payload out of a BraTS
# store, gzip level 1 ran at 0.09 GB/s for a ratio of 0.645, while SHA-256 over
# the same bytes ran at 4.50 GB/s. Compression, not hashing, is what that thread
# spends its time on.
#
# zstd level 3 on those same bytes: about 0.71 GB/s and a ratio of 0.440. EIGHT
# TIMES FASTER AND A THIRD SMALLER -- it wins on both axes, which is unusual
# enough to be worth the measurement being written down. On a sweep that once
# overran its free space by 1.9 TB, a third is some 600 GB.
#
# READS STAY SELF-DESCRIBING, and this is what makes the change safe: a payload
# is decompressed according to the MAGIC BYTES it carries. Existing stores are
# full of gzip and keep decoding; new writes are zstd; a store written by either
# version is readable by this one. Uncompressed payloads from before any of this
# still pass through untouched.
_GZIP_LEVEL = 1
_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_LEVEL = 3
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _zstd():
    """The zstandard module, or None if it is not installed.

    Optional on purpose: a missing wheel must degrade to gzip rather than break
    a run, and a store written by a build without it stays readable by one with
    it and the other way round.
    """
    global _ZSTD_MODULE
    if _ZSTD_MODULE is _UNPROBED:
        try:
            import zstandard  # type: ignore
        except Exception:  # noqa: BLE001
            _ZSTD_MODULE = None
        else:
            _ZSTD_MODULE = zstandard
    return _ZSTD_MODULE


_UNPROBED = object()
_ZSTD_MODULE: Any = _UNPROBED


def _compress(raw: Any) -> bytes:
    module = _zstd()
    if module is None:
        return gzip.compress(raw, compresslevel=_GZIP_LEVEL)
    return module.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw)


def _decompress(payload_bin: bytes | None) -> bytes:
    data = payload_bin or b""
    if data[:2] == _GZIP_MAGIC:
        return gzip.decompress(data)
    if data[:4] == _ZSTD_MAGIC:
        module = _zstd()
        if module is None:
            raise RuntimeError(
                "this payload is zstd-compressed and the zstandard module is not "
                "installed; the store was written by a build that had it")
        return module.ZstdDecompressor().decompress(data)
    return data


from voxlogica.handles import Handle, revive_handles
from voxlogica.value_model import (
    VoxHandleValue,
    OverlayLayer,
    OverlayValue,
    UnsupportedVoxValueError,
    VOX_FORMAT_VERSION,
    VoxBytesValue,
    VoxMappingValue,
    VoxNdArrayValue,
    VoxOverlayValue,
    VoxSequenceValue,
    VoxImageValue,
    adapt_runtime_value,
    normalize_overlay_layer,
    restore_runtime_image,
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


def _array_byte_view(array: Any) -> memoryview:
    """Contiguous byte view without ``ndarray.tobytes()``'s full-size copy."""
    view = memoryview(array)
    if not view.c_contiguous:
        np = _import_numpy()
        if np is None:
            raise RuntimeError("NumPy is required to serialize non-contiguous arrays")
        view = memoryview(np.ascontiguousarray(array))
    return view.cast("B")


def _ndarray_payload(array: Any) -> tuple[dict[str, Any], bytes]:
    return {
        "encoding": "ndarray-binary-v1",
        "dtype": str(array.dtype),
        "shape": [int(v) for v in array.shape],
        "order": "C",
        "byte_order": "little",
    }, _compress(_array_byte_view(array))


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


def encode_for_storage(value: Any, *, page_size: int = 128,
                       payload_snapshot: Any = None) -> EncodedRecord:
    """``payload_snapshot`` is an ALREADY-COPIED byte view of the image payload.

    Volumetric payloads alias memory owned by SimpleITK/ITK, and ITK frees that
    memory on its own schedule -- holding the Python image object does not keep
    the buffer alive. Compressing the live alias on a writer thread therefore
    races a worker's SimpleITK call. Proven by address match: the SIGSEGV
    address 0x7ffee6b82000 fell inside the block SimpleITK unmapped at that
    instant (addr=0x7ffee5df1000 len=35713024, _int_free_chunk <- _SimpleITK.so
    <- cfunction_call, on a worker thread). The caller takes the snapshot on
    the event loop, ordered with the kernels, and passes it here.
    """
    adapted = adapt_runtime_value(value)
    # The snapshot carries the shape facts precisely so describing an image does
    # not have to reach back into ITK memory from this thread. Only images have
    # one; everything else describes itself from memory Python owns.
    if payload_snapshot is not None and isinstance(adapted, VoxImageValue):
        descriptor = adapted.describe(path="", snapshot=payload_snapshot)
    else:
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

    if isinstance(adapted, VoxImageValue):
        # Same rule as the descriptor above: with a snapshot, dtype/shape come
        # from it and the live array is never materialised on this thread.
        if payload_snapshot is not None:
            dtype, shape = payload_snapshot.dtype, payload_snapshot.shape
            payload_bytes = payload_snapshot.data
        else:
            array = adapted.as_array()
            dtype, shape = str(array.dtype), array.shape
            payload_bytes = _array_byte_view(array)
        payload_json = {
            "encoding": "image-array-binary-v1",
            "dtype": str(dtype),
            "shape": [int(v) for v in shape],
            "order": "C",
            "byte_order": "little",
            "metadata": adapted.storage_metadata(),
        }
        payload_bin = _compress(payload_bytes)
        return EncodedRecord(VOX_FORMAT_VERSION, "image", descriptor, payload_json, payload_bin)

    if isinstance(adapted, VoxBytesValue):
        return EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type="bytes",
            descriptor=descriptor,
            payload_json={"encoding": "bytes-binary-v1", "length": len(value)},
            payload_bin=_compress(bytes(value)),
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

    if isinstance(adapted, VoxHandleValue):
        return EncodedRecord(
            VOX_FORMAT_VERSION,
            "handle",
            descriptor,
            {"encoding": "handle-json-v1", "node": adapted.raw.node},
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
        return _decompress(payload_bin)
    if vox_type == "mapping":
        return dict(payload_json.get("value") or {})
    if vox_type == "sequence":
        return revive_handles(list(payload_json.get("value") or []))
    if vox_type == "handle":
        return Handle(str(payload_json.get("node") or ""))
    if vox_type == "ndarray":
        if np is None:
            raise RuntimeError("NumPy is required to decode ndarray values.")
        dtype = np.dtype(str(payload_json["dtype"]))
        shape = tuple(int(v) for v in payload_json["shape"])
        return np.frombuffer(_decompress(payload_bin), dtype=dtype).reshape(shape, order="C")
    if vox_type == "image":
        if np is None:
            raise RuntimeError("NumPy is required to decode image values.")
        dtype = np.dtype(str(payload_json["dtype"]))
        shape = tuple(int(v) for v in payload_json["shape"])
        array = np.frombuffer(_decompress(payload_bin), dtype=dtype).reshape(shape, order="C")
        return restore_runtime_image(payload_json, array)
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
