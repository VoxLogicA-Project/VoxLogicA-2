"""Print primitive for VoxLogicA."""

from __future__ import annotations


def execute(**kwargs):
    """Print label=value and return the rendered string."""
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("print_primitive requires keys '0' (label) and '1' (value)")

    label = kwargs["0"]
    value = kwargs["1"]

    if isinstance(label, str) and label.startswith('"') and label.endswith('"'):
        label = label[1:-1]

    rendered = f"{label}={value}"
    print(rendered)
    return rendered
