"""SimpleITK primitive namespace facade."""

from voxlogica.primitives.simpleitk.runtime import (
    get_primitives,
    get_serializers,
    list_primitives,
    register_primitives,
    register_specs,
)

__all__ = [
    "get_primitives",
    "get_serializers",
    "list_primitives",
    "register_primitives",
    "register_specs",
]
