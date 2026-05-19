"""SimpleITK Statistics compatibility primitive."""

from __future__ import annotations

import math
from typing import Any

import SimpleITK as sitk

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(image: Any) -> tuple[float, float, float, float, float, float, int]:
    """Return scalar image statistics in a deterministic tuple order.

    Tuple layout:
    0=min, 1=max, 2=mean, 3=sigma, 4=variance, 5=sum, 6=count
    """
    if not hasattr(image, "GetDimension") or not hasattr(image, "GetSize"):
        raise ValueError(f"Statistics expects a SimpleITK image, got {type(image).__name__}")

    stats = sitk.StatisticsImageFilter()
    stats.Execute(image)
    pixel_count = int(math.prod(int(v) for v in image.GetSize()))
    return (
        float(stats.GetMinimum()),
        float(stats.GetMaximum()),
        float(stats.GetMean()),
        float(stats.GetSigma()),
        float(stats.GetVariance()),
        float(stats.GetSum()),
        pixel_count,
    )


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="Statistics",
    namespace="simpleitk",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("simpleitk.Statistics", kind="scalar"),
    kernel_name="simpleitk.Statistics",
    description="SimpleITK image statistics: min, max, mean, sigma, variance, sum, count",
)
