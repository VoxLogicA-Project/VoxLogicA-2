"""Functional 2D geometry drawing primitives."""

import importlib
from pathlib import Path


def register_specs():
    """Static namespace; primitive specs are provided by module files."""
    return {}


def register_primitives():
    """Legacy compatibility shim."""
    return {}


def list_primitives():
    """List primitives in this namespace."""
    primitives: dict[str, str] = {}
    namespace_dir = Path(__file__).parent
    for item in namespace_dir.iterdir():
        if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            module_name = item.stem
            try:
                module = importlib.import_module(f"voxlogica.primitives.geom.{module_name}")
                description = "No description available"
                if module.__doc__:
                    description = module.__doc__.strip().split("\n")[0]
                primitives[module_name] = description
            except Exception:
                primitives[module_name] = f"Primitive from {module_name}.py"
    return primitives
