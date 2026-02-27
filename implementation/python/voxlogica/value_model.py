"""VoxLogicA runtime value model and descriptor contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence
import math


VOX_FORMAT_VERSION = "voxpod/1"
DEFAULT_PAGE_SIZE = 64
MAX_PAGE_SIZE = 512


class VoxValueError(RuntimeError):
    """Base runtime error for Vox value handling."""


class UnsupportedVoxValueError(VoxValueError):
    """Raised when a runtime value cannot be represented by the store spec."""

    code = "E_UNSPECIFIED_VALUE_TYPE"

    def __init__(self, value: Any, message: str | None = None):
        type_name = f"{type(value).__module__}.{type(value).__name__}"
        detail = message or f"Value type '{type_name}' is not supported by voxpod/1."
        super().__init__(detail)
        self.value_type = type_name


def normalize_path(path: str | None) -> str:
    raw = str(path or "").strip()
    if not raw or raw == "/":
        return ""
    if raw.startswith("/"):
        return raw
    return f"/{raw}"


def _decode_path_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _encode_path_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def append_path(base: str, token: str) -> str:
    encoded = _encode_path_token(str(token))
    if not base:
        return f"/{encoded}"
    return f"{base}/{encoded}"


def _path_tokens(path: str) -> list[str]:
    normalized = normalize_path(path)
    if not normalized:
        return []
    return [_decode_path_token(token) for token in normalized.split("/") if token]


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class VoxPage:
    """Lazy page payload for a sequence-like value."""

    offset: int
    limit: int
    items: list[Any]
    has_more: bool
    next_offset: int | None
    total: int | None


class VoxValue(ABC):
    """Object-oriented adapter for runtime values."""

    vox_type: str = "unknown"

    def __init__(self, raw: Any):
        self.raw = raw

    @abstractmethod
    def describe(self, *, path: str = "") -> dict[str, Any]:
        """Return canonical descriptor for this value."""

    def page(self, *, offset: int, limit: int) -> VoxPage:
        raise VoxValueError(f"Value type '{self.vox_type}' does not support paging")

    def resolve(self, *, path: str = "") -> "VoxValue":
        tokens = _path_tokens(path)
        if not tokens:
            return self
        raise VoxValueError(f"Cannot descend into value type '{self.vox_type}' using path '{path}'")

    def to_json_native(self) -> Any:
        raise VoxValueError(f"Value type '{self.vox_type}' does not support JSON-native serialization")

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


class VoxNullValue(VoxValue):
    vox_type = "null"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"value": None}
        return payload

    def to_json_native(self) -> Any:
        return None


class VoxBooleanValue(VoxValue):
    vox_type = "boolean"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"value": bool(self.raw)}
        return payload

    def to_json_native(self) -> Any:
        return bool(self.raw)


class VoxIntegerValue(VoxValue):
    vox_type = "integer"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"value": int(self.raw)}
        return payload

    def to_json_native(self) -> Any:
        return int(self.raw)


class VoxNumberValue(VoxValue):
    vox_type = "number"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"value": float(self.raw)}
        return payload

    def to_json_native(self) -> Any:
        return float(self.raw)


class VoxStringValue(VoxValue):
    vox_type = "string"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        text = str(self.raw)
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"length": len(text), "value": text[:2048], "truncated": len(text) > 2048}
        return payload

    def to_json_native(self) -> Any:
        return str(self.raw)


class VoxBytesValue(VoxValue):
    vox_type = "bytes"

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path)
        payload["summary"] = {"length": len(self.raw)}
        return payload


class VoxNdArrayValue(VoxValue):
    vox_type = "ndarray"

    def __init__(self, raw: Any):
        super().__init__(raw)
        self._np = _import_numpy()
        if self._np is None:
            raise UnsupportedVoxValueError(raw, "NumPy is required to handle ndarray values.")

    def describe(self, *, path: str = "") -> dict[str, Any]:
        arr = self.raw
        payload = self.descriptor_base(path=path)
        summary: dict[str, Any] = {
            "dtype": str(arr.dtype),
            "shape": [int(v) for v in arr.shape],
            "size": int(arr.size),
        }
        if arr.size and self._np.issubdtype(arr.dtype, self._np.number):
            sample = arr.reshape(-1)
            if sample.size > 200000:
                step = max(1, sample.size // 200000)
                sample = sample[::step]
            finite = sample
            if self._np.issubdtype(sample.dtype, self._np.floating):
                finite = sample[self._np.isfinite(sample)]
            if finite.size:
                summary["stats"] = {
                    "min": float(self._np.min(finite)),
                    "max": float(self._np.max(finite)),
                    "mean": float(self._np.mean(finite)),
                }
        payload["summary"] = summary
        if arr.ndim == 2:
            payload["render"] = {"kind": "image2d"}
        elif arr.ndim == 3:
            payload["render"] = {"kind": "medical-volume"}
        return payload


class VoxImageValue(VoxValue):
    """SimpleITK image adapter."""

    def describe(self, *, path: str = "") -> dict[str, Any]:
        dimension = int(self.raw.GetDimension())
        size = [int(v) for v in self.raw.GetSize()]
        spacing = [float(v) for v in self.raw.GetSpacing()]
        origin = [float(v) for v in self.raw.GetOrigin()]
        direction = [float(v) for v in self.raw.GetDirection()]
        pixel_id = str(self.raw.GetPixelIDTypeAsString())
        vox_type = "image2d" if dimension == 2 else "volume3d"
        payload = self.descriptor_base(path=path)
        payload["vox_type"] = vox_type
        payload["summary"] = {
            "dimension": dimension,
            "size": size,
            "spacing": spacing,
            "origin": origin,
            "direction": direction,
            "pixel_id": pixel_id,
        }
        if vox_type == "image2d":
            payload["render"] = {"kind": "image2d"}
        else:
            payload["render"] = {"kind": "medical-volume"}
        return payload

    @property
    def vox_type(self) -> str:  # type: ignore[override]
        return "image2d" if int(self.raw.GetDimension()) == 2 else "volume3d"


class VoxMappingValue(VoxValue):
    vox_type = "mapping"

    def __init__(self, raw: dict[Any, Any]):
        super().__init__(raw)
        self._mapping = raw

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path, pageable=True, can_descend=True)
        keys = list(self._mapping.keys())
        payload["summary"] = {
            "length": len(keys),
            "keys_preview": [str(key) for key in keys[:16]],
            "truncated": len(keys) > 16,
        }
        return payload

    def page(self, *, offset: int, limit: int) -> VoxPage:
        keys = list(self._mapping.keys())
        start = max(0, int(offset))
        end = max(start, start + max(0, int(limit)))
        selected = keys[start:end]
        items = []
        for key in selected:
            path = append_path("", str(key))
            adapted = adapt_runtime_value(self._mapping[key])
            descriptor = adapted.describe(path=path)
            entry: dict[str, Any] = {"label": str(key), "path": path, "descriptor": descriptor}
            try:
                entry["value"] = adapted.to_json_native()
            except VoxValueError:
                pass
            items.append(entry)
        next_offset = end if end < len(keys) else None
        return VoxPage(
            offset=start,
            limit=max(0, int(limit)),
            items=items,
            has_more=(end < len(keys)),
            next_offset=next_offset,
            total=len(keys),
        )

    def resolve(self, *, path: str = "") -> VoxValue:
        tokens = _path_tokens(path)
        if not tokens:
            return self
        key = tokens[0]
        if key not in self._mapping:
            raise VoxValueError(f"Missing key '{key}' in path '{path}'")
        child = adapt_runtime_value(self._mapping[key])
        if len(tokens) == 1:
            return child
        remainder = "/" + "/".join(_encode_path_token(token) for token in tokens[1:])
        return child.resolve(path=remainder)

    def to_json_native(self) -> Any:
        out: dict[str, Any] = {}
        for key, value in self._mapping.items():
            if not isinstance(key, str):
                raise UnsupportedVoxValueError(value, "Mapping keys must be strings for JSON serialization.")
            out[key] = adapt_runtime_value(value).to_json_native()
        return out


class VoxSequenceValue(VoxValue, ABC):
    vox_type = "sequence"

    @abstractmethod
    def _len(self) -> int | None:
        """Return known length when available."""

    @abstractmethod
    def _iter_window(self, offset: int, limit: int) -> tuple[list[Any], bool]:
        """Return items and whether more items exist beyond window."""

    def describe(self, *, path: str = "") -> dict[str, Any]:
        payload = self.descriptor_base(path=path, pageable=True, can_descend=True)
        payload["summary"] = {"length": self._len()}
        return payload

    def page(self, *, offset: int, limit: int) -> VoxPage:
        safe_offset = max(0, int(offset))
        safe_limit = max(0, min(int(limit), MAX_PAGE_SIZE))
        items_raw, has_more = self._iter_window(safe_offset, safe_limit)
        items = []
        for index, value in enumerate(items_raw):
            absolute_index = safe_offset + index
            item_path = append_path("", str(absolute_index))
            adapted = adapt_runtime_value(value)
            descriptor = adapted.describe(path=item_path)
            entry: dict[str, Any] = {"label": f"[{absolute_index}]", "path": item_path, "descriptor": descriptor}
            try:
                entry["value"] = adapted.to_json_native()
            except VoxValueError:
                pass
            # Keep the original value available for in-process persistence adapters
            # that need richer serialization than JSON-native inline payloads.
            entry["_raw"] = value
            items.append(entry)
        total = self._len()
        next_offset = safe_offset + len(items)
        if not has_more:
            next_offset = None
        return VoxPage(
            offset=safe_offset,
            limit=safe_limit,
            items=items,
            has_more=bool(has_more),
            next_offset=next_offset,
            total=total,
        )

    def resolve(self, *, path: str = "") -> VoxValue:
        tokens = _path_tokens(path)
        if not tokens:
            return self
        try:
            index = int(tokens[0])
        except ValueError as exc:
            raise VoxValueError(f"Invalid sequence index '{tokens[0]}' in path '{path}'") from exc
        if index < 0:
            raise VoxValueError(f"Invalid negative index '{index}' in path '{path}'")
        window, _has_more = self._iter_window(index, 1)
        if not window:
            raise VoxValueError(f"Sequence index out of range in path '{path}'")
        child = adapt_runtime_value(window[0])
        if len(tokens) == 1:
            return child
        remainder = "/" + "/".join(_encode_path_token(token) for token in tokens[1:])
        return child.resolve(path=remainder)

    def to_json_native(self) -> Any:
        items, has_more = self._iter_window(0, MAX_PAGE_SIZE + 1)
        if has_more:
            raise UnsupportedVoxValueError(self.raw, "Lazy sequence is too large for JSON-native serialization.")
        return [adapt_runtime_value(item).to_json_native() for item in items]


class VoxPythonSequenceValue(VoxSequenceValue):
    """Sequence wrapper for list/tuple/range."""

    def _len(self) -> int | None:
        return len(self.raw)

    def _iter_window(self, offset: int, limit: int) -> tuple[list[Any], bool]:
        if limit <= 0:
            return [], offset < len(self.raw)
        end = min(len(self.raw), offset + limit)
        items = [self.raw[index] for index in range(offset, end)]
        has_more = end < len(self.raw)
        return items, has_more


class VoxIteratorSequenceValue(VoxSequenceValue):
    """Sequence wrapper for SequenceValue style iterators."""

    def __init__(self, raw: Any):
        super().__init__(raw)
        self._total_size = getattr(raw, "total_size", None)

    def _len(self) -> int | None:
        if self._total_size is None:
            return None
        try:
            return int(self._total_size)
        except Exception:
            return None

    def _iter_window(self, offset: int, limit: int) -> tuple[list[Any], bool]:
        values: list[Any] = []
        consumed = 0
        seen_more = False
        iterator = self.raw.iter_values() if hasattr(self.raw, "iter_values") else iter(self.raw)
        for item in iterator:
            if consumed < offset:
                consumed += 1
                continue
            if len(values) < limit:
                values.append(item)
                consumed += 1
                continue
            seen_more = True
            break
        if not seen_more:
            length = self._len()
            if length is not None and offset + len(values) < length:
                seen_more = True
        return values, seen_more


class VoxDaskBagSequenceValue(VoxSequenceValue):
    """Preferred sequence wrapper for dask.bag values."""

    def _len(self) -> int | None:
        return None

    def _iter_window(self, offset: int, limit: int) -> tuple[list[Any], bool]:
        values: list[Any] = []
        cursor = 0
        has_more = False
        for delayed_partition in self.raw.to_delayed():
            partition_items = delayed_partition.compute()
            for item in partition_items:
                if cursor < offset:
                    cursor += 1
                    continue
                if len(values) < limit:
                    values.append(item)
                    cursor += 1
                    continue
                has_more = True
                return values, has_more
        return values, has_more


def _import_numpy():
    try:
        import numpy as np

        return np
    except Exception:
        return None


def _is_simpleitk_image_like(value: Any) -> bool:
    try:
        import SimpleITK as sitk  # type: ignore

        if isinstance(value, sitk.Image):
            return True
    except Exception:
        pass

    required = (
        "GetDimension",
        "GetSize",
        "GetSpacing",
        "GetOrigin",
        "GetDirection",
        "GetPixelIDTypeAsString",
    )
    for name in required:
        method = getattr(value, name, None)
        if not callable(method):
            return False
    return True


def _is_dask_bag(value: Any) -> bool:
    try:
        import dask.bag as db  # type: ignore

        return isinstance(value, db.Bag)
    except Exception:
        return False


def _is_sequence_value(value: Any) -> bool:
    try:
        from voxlogica.execution_strategy.results import SequenceValue

        return isinstance(value, SequenceValue)
    except Exception:
        return False


def adapt_runtime_value(value: Any) -> VoxValue:
    """Adapt a native runtime value to the canonical Vox model."""
    np = _import_numpy()

    if value is None:
        return VoxNullValue(value)
    if isinstance(value, bool):
        return VoxBooleanValue(value)
    if isinstance(value, int):
        return VoxIntegerValue(value)
    if isinstance(value, float):
        return VoxNumberValue(value)
    if isinstance(value, str):
        return VoxStringValue(value)
    if isinstance(value, bytes):
        return VoxBytesValue(value)
    if np is not None and isinstance(value, np.ndarray):
        return VoxNdArrayValue(value)
    if _is_simpleitk_image_like(value):
        return VoxImageValue(value)
    if isinstance(value, dict):
        return VoxMappingValue(value)
    if _is_dask_bag(value):
        return VoxDaskBagSequenceValue(value)
    if _is_sequence_value(value):
        return VoxIteratorSequenceValue(value)
    if isinstance(value, (list, tuple, range)):
        return VoxPythonSequenceValue(value)
    raise UnsupportedVoxValueError(value)
