"""Strings primitive namespace for formatting and concatenation."""

import importlib
from pathlib import Path


def register_specs():
    """Static namespace; primitive specs are provided by module files."""
    return {}


def register_primitives():
    """Legacy compatibility shim."""
    return {}


def list_primitives():
    """List all primitives available in this namespace."""
    primitives = {}
    namespace_dir = Path(__file__).parent

    for item in namespace_dir.iterdir():
        if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            module_name = item.stem
            try:
                module_path = f"voxlogica.primitives.strings.{module_name}"
                module = importlib.import_module(module_path)

                description = "No description available"
                if hasattr(module, "__doc__") and module.__doc__:
                    description = module.__doc__.strip().split("\n")[0]
                elif hasattr(module, "execute") and module.execute.__doc__:
                    description = module.execute.__doc__.strip().split("\n")[0]

                primitives[module_name] = description
            except Exception:
                primitives[module_name] = f"Primitive from {module_name}.py"

    return primitives

