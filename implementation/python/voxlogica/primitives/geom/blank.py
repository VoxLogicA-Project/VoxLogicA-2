"""Create a blank 2D canvas for geometry primitives."""

from __future__ import annotations

import numpy as np
import SimpleITK as sitk

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.geom._draw import as_float, as_int


def execute(**kwargs):
    """Return a new 2D float image filled with a constant value."""
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("blank requires height and width")
    height = as_int(kwargs["0"], "height")
    width = as_int(kwargs["1"], "width")
    value = as_float(kwargs.get("2", 0), "value")
    if height <= 0 or width <= 0:
        raise ValueError("blank height and width must be positive")

    array = np.full((height, width), value, dtype=np.float32)
    image = sitk.GetImageFromArray(array)
    image.SetSpacing((1.0, 1.0))
    image.SetOrigin((0.0, 0.0))
    return image


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="blank",
    namespace="geom",
    kind="scalar",
    arity=AritySpec(min_args=2, max_args=3),
    attrs_schema={},
    planner=default_planner_factory("geom.blank", kind="scalar"),
    kernel_name="geom.blank",
    description="Create a blank 2D canvas",
)
