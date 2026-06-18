"""Draw a filled circle onto a 2D image."""

from __future__ import annotations

import numpy as np

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.geom._draw import as_float, as_image, as_int, copy_array, image_from_array


def execute(**kwargs):
    """Return a new image with a filled circle drawn on a copy of the input."""
    for key in ("0", "1", "2", "3", "4"):
        if key not in kwargs:
            raise ValueError("circle requires image, cx, cy, radius, value")

    image = as_image(kwargs["0"], "image")
    cx = as_int(kwargs["1"], "cx")
    cy = as_int(kwargs["2"], "cy")
    radius = as_int(kwargs["3"], "radius")
    value = as_float(kwargs["4"], "value")
    if radius <= 0:
        raise ValueError("circle radius must be positive")

    array = copy_array(image)
    height, width = array.shape
    yy, xx = np.ogrid[:height, :width]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2
    array[mask] = value
    return image_from_array(array, image)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="circle",
    namespace="geom",
    kind="scalar",
    arity=AritySpec.fixed(5),
    attrs_schema={},
    planner=default_planner_factory("geom.circle", kind="scalar"),
    kernel_name="geom.circle",
    description="Draw a filled circle on a 2D image",
)
