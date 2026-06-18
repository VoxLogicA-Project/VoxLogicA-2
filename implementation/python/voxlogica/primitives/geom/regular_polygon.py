"""Draw a filled regular polygon onto a 2D image."""

from __future__ import annotations

import numpy as np

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.geom._draw import (
    as_float,
    as_image,
    as_int,
    copy_array,
    image_from_array,
    inside_regular_polygon,
)


def execute(**kwargs):
    """Return a new image with a filled regular polygon drawn on a copy of the input."""
    for key in ("0", "1", "2", "3", "4", "5"):
        if key not in kwargs:
            raise ValueError("regular_polygon requires image, cx, cy, radius, sides, value")

    image = as_image(kwargs["0"], "image")
    cx = as_int(kwargs["1"], "cx")
    cy = as_int(kwargs["2"], "cy")
    radius = as_int(kwargs["3"], "radius")
    sides = as_int(kwargs["4"], "sides")
    value = as_float(kwargs["5"], "value")
    if radius <= 0:
        raise ValueError("regular_polygon radius must be positive")

    array = copy_array(image)
    height, width = array.shape
    yy, xx = np.ogrid[:height, :width]
    mask = inside_regular_polygon(xx - cx, yy - cy, float(radius), sides)
    array[mask] = value
    return image_from_array(array, image)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="regular_polygon",
    namespace="geom",
    kind="scalar",
    arity=AritySpec.fixed(6),
    attrs_schema={},
    planner=default_planner_factory("geom.regular_polygon", kind="scalar"),
    kernel_name="geom.regular_polygon",
    description="Draw a filled regular polygon on a 2D image",
)
