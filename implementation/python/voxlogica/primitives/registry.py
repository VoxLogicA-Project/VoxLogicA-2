"""Deterministic primitive discovery and resolution registry."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
import importlib
import inspect
import logging
import warnings

from voxlogica.parser import Command, parse_program_content
from voxlogica.primitives.api import (
    AritySpec,
    KernelFn,
    PrimitiveCall,
    PrimitiveSpec,
    default_planner_factory,
    validate_spec,
)

logger = logging.getLogger(__name__)

_FORBIDDEN_KERNEL_PARAMS = {"engine", "storage", "session"}


class PrimitiveRegistry:
    """Registry with deterministic namespace loading and name resolution."""

    def __init__(self, primitives_dir: Path | None = None) -> None:
        if primitives_dir is None:
            primitives_dir = Path(__file__).parent

        self.primitives_dir = primitives_dir
        self._specs_by_qualified: OrderedDict[str, PrimitiveSpec] = OrderedDict()
        self._kernels_by_name: dict[str, KernelFn] = {}
        self._specs_by_namespace: dict[str, OrderedDict[str, PrimitiveSpec]] = {}
        self._import_order: list[str] = []
        self._namespace_modules: dict[str, Any] = {}
        self._imgql_exports_by_namespace: dict[str, tuple[Command, ...]] = {}
        self._loaded_namespaces: set[str] = set()
        self._legacy_warning_emitted: set[str] = set()

        self._discover_namespaces()
        self.import_namespace("default")

    @property
    def imported_namespaces(self) -> tuple[str, ...]:
        return tuple(self._import_order)

    def _discover_namespaces(self) -> None:
        if not self.primitives_dir.exists():
            return
        for item in sorted(self.primitives_dir.iterdir(), key=lambda p: p.name):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            self._load_namespace(item.name)

    def _load_namespace(self, namespace: str) -> None:
        if namespace in self._loaded_namespaces:
            return

        namespace_dir = self.primitives_dir / namespace
        if not namespace_dir.exists() or not namespace_dir.is_dir():
            raise ValueError(f"Unknown primitive namespace: {namespace}")

        module_path = f"voxlogica.primitives.{namespace}"
        namespace_module = importlib.import_module(module_path)
        self._namespace_modules[namespace] = namespace_module
        namespace_specs = self._specs_by_namespace.setdefault(namespace, OrderedDict())
        namespace_exports: list[Command] = []

        for py_file in sorted(namespace_dir.glob("*.py"), key=lambda p: p.name):
            if py_file.name.startswith("_"):
                continue
            primitive_name = py_file.stem
            module_name = f"{module_path}.{primitive_name}"
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                logger.warning(
                    "Failed loading primitive module %s: %s", module_name, exc
                )
                continue

            spec_and_kernel = self._extract_spec_from_module(
                namespace=namespace,
                primitive_name=primitive_name,
                module=module,
                module_name=module_name,
            )
            if spec_and_kernel is None:
                continue

            spec, kernel = spec_and_kernel
            self.register(spec, kernel)
            namespace_specs[spec.name] = spec

        if hasattr(namespace_module, "register_specs"):
            registered_specs = namespace_module.register_specs()
            for primitive_name in sorted(registered_specs.keys()):
                spec, kernel = registered_specs[primitive_name]
                self.register(spec, kernel)
                namespace_specs[spec.name] = spec
        elif hasattr(namespace_module, "register_primitives"):
            dynamic = namespace_module.register_primitives()
            if isinstance(dynamic, dict):
                self._emit_legacy_warning(module_path)
                for primitive_name in sorted(dynamic.keys()):
                    kernel = dynamic[primitive_name]
                    spec = self._legacy_spec(namespace, primitive_name, kernel)
                    self.register(spec, kernel)
                    namespace_specs[spec.name] = spec

        for imgql_file in sorted(namespace_dir.glob("*.imgql"), key=lambda p: p.name):
            try:
                program = parse_program_content(imgql_file.read_text(encoding="utf-8"))
                namespace_exports.extend(program.commands)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed loading primitive imgql module %s: %s", imgql_file, exc
                )
        self._imgql_exports_by_namespace[namespace] = tuple(namespace_exports)

        self._loaded_namespaces.add(namespace)

    def _extract_spec_from_module(
        self,
        namespace: str,
        primitive_name: str,
        module: Any,
        module_name: str,
    ) -> tuple[PrimitiveSpec, KernelFn] | None:
        if hasattr(module, "PRIMITIVE_SPEC"):
            spec = module.PRIMITIVE_SPEC
            if not isinstance(spec, PrimitiveSpec):
                raise TypeError(f"{module_name}.PRIMITIVE_SPEC must be PrimitiveSpec")
            kernel = getattr(module, "KERNEL", None) or getattr(module, "execute", None)
            if kernel is None:
                raise ValueError(
                    f"{module_name} provides PRIMITIVE_SPEC but no KERNEL/execute"
                )
            return spec, kernel

        if hasattr(module, "build_primitive_spec"):
            spec, kernel = module.build_primitive_spec()
            if not isinstance(spec, PrimitiveSpec):
                raise TypeError(
                    f"{module_name}.build_primitive_spec() must return PrimitiveSpec"
                )
            return spec, kernel

        if hasattr(module, "execute"):
            self._emit_legacy_warning(module_name)
            kernel = module.execute
            spec = self._legacy_spec(namespace, primitive_name, kernel)
            return spec, kernel

        return None

    def _emit_legacy_warning(self, module_name: str) -> None:
        if module_name in self._legacy_warning_emitted:
            return
        warnings.warn(
            (
                f"Legacy primitive contract loaded from '{module_name}'. "
                "Please migrate to PrimitiveSpec + KERNEL."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        self._legacy_warning_emitted.add(module_name)

    def _legacy_spec(
        self,
        namespace: str,
        primitive_name: str,
        kernel: KernelFn,
    ) -> PrimitiveSpec:
        arity = _infer_arity(kernel)
        qualified_name = f"{namespace}.{primitive_name}"
        return PrimitiveSpec(
            name=primitive_name,
            namespace=namespace,
            kind="scalar",
            arity=arity,
            attrs_schema={},
            planner=default_planner_factory(qualified_name),
            kernel_name=qualified_name,
            description="Legacy adapter primitive",
            is_legacy_adapter=True,
        )

    def register(self, spec: PrimitiveSpec, kernel: KernelFn) -> None:
        validate_spec(spec)

        qualified_name = spec.qualified_name
        if qualified_name in self._specs_by_qualified:
            raise ValueError(f"Primitive already registered: {qualified_name}")

        if spec.kernel_name in self._kernels_by_name:
            raise ValueError(f"Kernel name already registered: {spec.kernel_name}")

        if not spec.is_legacy_adapter:
            _validate_kernel_signature(spec, kernel)

        self._specs_by_qualified[qualified_name] = spec
        self._kernels_by_name[spec.kernel_name] = kernel
        self._specs_by_namespace.setdefault(spec.namespace, OrderedDict())[spec.name] = spec

    def import_namespace(self, namespace: str) -> None:
        if namespace not in self._loaded_namespaces:
            self._load_namespace(namespace)

        if namespace not in self._import_order:
            self._import_order.append(namespace)

    def apply_imports(self, imported_namespaces: list[str] | tuple[str, ...]) -> None:
        for namespace in imported_namespaces:
            self.import_namespace(namespace)

    def namespace_imgql_exports(self, namespace: str) -> tuple[Command, ...]:
        if namespace not in self._loaded_namespaces:
            self._load_namespace(namespace)
        return self._imgql_exports_by_namespace.get(namespace, ())

    def resolve(self, name: str) -> PrimitiveSpec:
        if "." in name:
            namespace, primitive_name = name.split(".", 1)
            if namespace and primitive_name and namespace in self._specs_by_namespace:
                qualified = f"{namespace}.{primitive_name}"
                if qualified not in self._specs_by_qualified:
                    raise KeyError(f"Unknown primitive: {qualified}")
                return self._specs_by_qualified[qualified]

        ordered = []
        if "default" in self._import_order:
            ordered.append("default")
        for namespace in self._import_order:
            if namespace != "default":
                ordered.append(namespace)

        # Ensure deterministic behavior even for not-yet-imported namespaces.
        for namespace in sorted(self._specs_by_namespace.keys()):
            if namespace not in ordered:
                ordered.append(namespace)

        for namespace in ordered:
            specs = self._specs_by_namespace.get(namespace)
            if specs and name in specs:
                return specs[name]

        raise KeyError(f"Unknown primitive: {name}")

    def load_kernel(self, name: str) -> KernelFn:
        spec = self.resolve(name)
        return self._kernels_by_name[spec.kernel_name]

    def load_primitive(self, name: str) -> KernelFn:
        """Compatibility method used by existing execution code."""
        return self.load_kernel(name)

    def get_spec(self, name: str) -> PrimitiveSpec:
        return self.resolve(name)

    def list_namespaces(self) -> list[str]:
        return sorted(self._specs_by_namespace.keys())

    def list_primitives(self, namespace_name: str | None = None) -> dict[str, str]:
        if namespace_name is not None:
            if namespace_name not in self._loaded_namespaces:
                self._load_namespace(namespace_name)

            selected_specs: OrderedDict[str, PrimitiveSpec] = self._specs_by_namespace.get(
                namespace_name,
                OrderedDict(),
            )
            return {
                name: spec.description or "Primitive"
                for name, spec in selected_specs.items()
            }

        output: dict[str, str] = {}
        for namespace in self.list_namespaces():
            namespace_specs: OrderedDict[str, PrimitiveSpec] = self._specs_by_namespace.get(
                namespace,
                OrderedDict(),
            )
            for primitive_name, spec in namespace_specs.items():
                output[f"{namespace}.{primitive_name}"] = spec.description or "Primitive"
        return output

    def reset_runtime_state(self) -> None:
        for namespace in self.list_namespaces():
            namespace_module = self._namespace_modules.get(namespace)
            if namespace_module is None:
                continue
            reset = getattr(namespace_module, "reset_runtime_state", None)
            if callable(reset):
                reset()


def _infer_arity(kernel: KernelFn) -> AritySpec:
    signature = inspect.signature(kernel)
    required = 0
    optional = 0
    has_varargs = False
    has_varkw = False

    for parameter in signature.parameters.values():
        kind = parameter.kind
        if kind == inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True
            continue
        if kind == inspect.Parameter.VAR_KEYWORD:
            has_varkw = True
            continue
        if parameter.default is inspect.Parameter.empty:
            required += 1
        else:
            optional += 1

    if has_varargs or has_varkw:
        return AritySpec.variadic(min_args=required)

    return AritySpec(min_args=required, max_args=required + optional)


def _validate_kernel_signature(spec: PrimitiveSpec, kernel: KernelFn) -> None:
    signature = inspect.signature(kernel)
    forbidden = _FORBIDDEN_KERNEL_PARAMS.intersection(signature.parameters.keys())
    if forbidden:
        joined = ", ".join(sorted(forbidden))
        raise ValueError(
            f"Primitive '{spec.qualified_name}' kernel cannot depend on runtime internals ({joined})"
        )


def adapt_legacy_execute(kernel: KernelFn) -> Callable[[dict[str, Any]], Any]:
    """Adapter helper for legacy execute(**kwargs) style kernels."""

    def _adapted(arguments: dict[str, Any]) -> Any:
        return kernel(**arguments)

    return _adapted


def primitive_call_from_refs(
    args: tuple[str, ...], kwargs: dict[str, str] | None = None, attrs: dict[str, Any] | None = None
) -> PrimitiveCall:
    return PrimitiveCall(
        args=args,
        kwargs=tuple(sorted((kwargs or {}).items())),
        attrs=dict(attrs or {}),
    )
