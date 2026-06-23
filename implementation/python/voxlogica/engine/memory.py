"""Memory-pressure sensing for the live value tier.

Values are kept in RAM as long as they fit; eviction is driven by a byte budget,
not by eager consumer counting. Because every value is recomputable from its
recipe, evicting under pressure only ever costs a rematerialisation.

Sizes are estimated (images dominate; scalars are negligible), which is stable
and cheap — unlike process RSS, which lags object collection.
"""

from __future__ import annotations

import os


def estimate_bytes(value: object) -> int:
    """Approximate the resident size of one materialized value."""
    if hasattr(value, "GetNumberOfPixels"):  # SimpleITK image (duck-typed)
        try:
            pixels = value.GetNumberOfPixels() * value.GetNumberOfComponentsPerPixel()
            return pixels * 4  # ~float32-equivalent; rough but consistent
        except Exception:  # noqa: BLE001
            return 4_000_000
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if hasattr(value, "nbytes"):  # numpy array
        return int(value.nbytes)
    if isinstance(value, (list, tuple)):
        return 64 + sum(estimate_bytes(item) for item in value)
    return 64  # scalars and small objects


def memory_limit_bytes() -> int:
    """The live-tier budget: VOXLOGICA_ENGINE_MEMORY_MB, else 60% of system RAM."""
    override = os.environ.get("VOXLOGICA_ENGINE_MEMORY_MB")
    if override:
        return int(override) * 1024 * 1024
    total = _system_memory_bytes()
    return int(total * 0.6) if total else 4 * 1024 ** 3


def _system_memory_bytes() -> int:
    """Total physical memory in bytes, or 0 if it cannot be determined."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 0
