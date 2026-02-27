"""Static diagnostics and runtime policy enforcement utilities."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import os

from voxlogica.lazy.ir import NodeSpec
from voxlogica.parser import ECall, EFor, ELet, Expression, parse_expression_content
from voxlogica.primitives.api import PrimitiveSpec
from voxlogica.primitives.registry import PrimitiveRegistry

SERVE_DATA_DIR_ENV = "VOXLOGICA_SERVE_DATA_DIR"
SERVE_EXTRA_READ_ROOTS_ENV = "VOXLOGICA_SERVE_EXTRA_READ_ROOTS"
_SERVE_LOAD_DIR_ENV = "VOXLOGICA_SERVE_LOAD_DIR"

_DEFAULT_SERVE_DATA_DIR = Path(__file__).resolve().parents[3] / "tests"
_SIMPLEITK_EFFECT_PREFIXES = (
    "Write",
    "ImageViewer_SetGlobalDefault",
    "ProcessObject_SetGlobal",
)
_READ_OPERATOR_NAMES = {"ReadImage", "ReadTransform", "load", "dir"}


@dataclass(frozen=True)
class StaticDiagnostic:
    """Machine-friendly static diagnostic."""

    code: str
    message: str
    location: str | None = None
    symbol: str | None = None


class StaticPolicyError(RuntimeError):
    """Raised when static diagnostics block execution."""

    def __init__(self, diagnostics: Iterable[StaticDiagnostic]):
        diagnostics_list = list(diagnostics)
        if not diagnostics_list:
            diagnostics_list = [
                StaticDiagnostic(
                    code="E_STATIC_POLICY",
                    message="Static policy check failed.",
                )
            ]
        self.diagnostics = tuple(diagnostics_list)
        super().__init__(self.diagnostics[0].message)


class StaticAnalysisError(StaticPolicyError):
    """Raised when static resolution fails before execution."""


@dataclass(frozen=True)
class RuntimePolicyContext:
    """Runtime policy controls used by strict/dask execution paths."""

    serve_mode: bool
    allowed_read_roots: tuple[Path, ...]


_RUNTIME_POLICY: ContextVar[RuntimePolicyContext | None] = ContextVar(
    "voxlogica_runtime_policy",
    default=None,
)


def diagnostics_payload(diagnostics: Iterable[StaticDiagnostic]) -> list[dict[str, Any]]:
    """Serialize diagnostics for API payloads."""
    payload: list[dict[str, Any]] = []
    for diag in diagnostics:
        item: dict[str, Any] = {
            "code": str(diag.code),
            "message": str(diag.message),
        }
        if diag.location:
            item["location"] = str(diag.location)
        if diag.symbol:
            item["symbol"] = str(diag.symbol)
        payload.append(item)
    return payload


def diagnostics_from_exception(exc: Exception) -> list[dict[str, Any]]:
    """Return structured diagnostics from known static exceptions."""
    if isinstance(exc, StaticPolicyError):
        return diagnostics_payload(exc.diagnostics)

    message = str(exc).strip() or exc.__class__.__name__
    return diagnostics_payload(
        [
            StaticDiagnostic(
                code="E_STATIC_ANALYSIS",
                message=message,
            )
        ]
    )


def resolve_serve_read_roots() -> tuple[Path, ...]:
    """Resolve serve-mode allowed read roots from environment variables."""
    configured = os.environ.get(SERVE_DATA_DIR_ENV, "").strip()
    if configured:
        primary = Path(configured).expanduser().resolve()
    else:
        legacy_load_dir = os.environ.get(_SERVE_LOAD_DIR_ENV, "").strip()
        if legacy_load_dir:
            primary = Path(legacy_load_dir).expanduser().resolve()
        else:
            primary = _DEFAULT_SERVE_DATA_DIR.resolve()

    extras_raw = os.environ.get(SERVE_EXTRA_READ_ROOTS_ENV, "")
    roots: list[Path] = [primary]
    for token in extras_raw.split(","):
        stripped = token.strip()
        if not stripped:
            continue
        roots.append(Path(stripped).expanduser().resolve())

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        marker = str(root)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(root)
    return tuple(deduped)


def _operator_leaf(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def is_read_operator(name: str) -> bool:
    """Return True when operator is a read-like primitive."""
    return _operator_leaf(name) in _READ_OPERATOR_NAMES


def is_effectful_primitive(spec: PrimitiveSpec, operator_name: str) -> bool:
    """Return True when primitive should be blocked in non-legacy mode."""
    if spec.kind == "effect":
        return True

    if spec.namespace != "simpleitk":
        return False

    leaf = _operator_leaf(operator_name)
    return any(leaf.startswith(prefix) for prefix in _SIMPLEITK_EFFECT_PREFIXES)


def _resolve_spec(registry: PrimitiveRegistry, name: str) -> PrimitiveSpec | None:
    try:
        return registry.get_spec(name)
    except Exception:
        return None


def _path_within_roots(candidate: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _read_root_error(
    *,
    operator_name: str,
    path_text: str,
    roots: tuple[Path, ...],
    location: str | None = None,
) -> StaticDiagnostic | None:
    candidate = Path(path_text).expanduser().resolve(strict=False)
    if _path_within_roots(candidate, roots):
        return None

    roots_text = ", ".join(str(root) for root in roots)
    return StaticDiagnostic(
        code="E_READ_ROOT_POLICY",
        message=(
            f"Serve read policy blocked '{operator_name}' for path '{candidate}'. "
            f"Allowed roots: {roots_text}"
        ),
        location=location,
        symbol=operator_name,
    )


def _constant_string_argument(
    nodes: dict[str, NodeSpec],
    node: NodeSpec,
    index: int,
) -> str | None:
    if len(node.args) <= index:
        return None
    node_id = node.args[index]
    target = nodes.get(node_id)
    if target is None or target.kind != "constant":
        return None
    value = target.attrs.get("value")
    if isinstance(value, str):
        return value
    return None


def _append_effect_diagnostic(
    diagnostics: list[StaticDiagnostic],
    *,
    operator_name: str,
    location: str | None,
) -> None:
    diagnostics.append(
        StaticDiagnostic(
            code="E_EFFECT_BLOCKED",
            message=(
                f"Primitive '{operator_name}' is blocked in non-legacy mode "
                "because it may produce side effects."
            ),
            location=location,
            symbol=operator_name,
        )
    )


def _scan_expression_for_effects(
    *,
    expression: Expression,
    bound_names: set[str],
    registry: PrimitiveRegistry,
    diagnostics: list[StaticDiagnostic],
    location: str,
) -> None:
    if isinstance(expression, ECall):
        identifier = str(expression.identifier)
        if identifier not in bound_names:
            spec = _resolve_spec(registry, identifier)
            if spec is not None and is_effectful_primitive(spec, identifier):
                _append_effect_diagnostic(
                    diagnostics,
                    operator_name=identifier,
                    location=location,
                )
        for arg in expression.arguments:
            _scan_expression_for_effects(
                expression=arg,
                bound_names=bound_names,
                registry=registry,
                diagnostics=diagnostics,
                location=location,
            )
        return

    if isinstance(expression, EFor):
        _scan_expression_for_effects(
            expression=expression.iterable,
            bound_names=bound_names,
            registry=registry,
            diagnostics=diagnostics,
            location=location,
        )
        scoped = set(bound_names)
        scoped.add(expression.variable)
        _scan_expression_for_effects(
            expression=expression.body,
            bound_names=scoped,
            registry=registry,
            diagnostics=diagnostics,
            location=location,
        )
        return

    if isinstance(expression, ELet):
        _scan_expression_for_effects(
            expression=expression.value,
            bound_names=bound_names,
            registry=registry,
            diagnostics=diagnostics,
            location=location,
        )
        scoped = set(bound_names)
        scoped.add(expression.variable)
        _scan_expression_for_effects(
            expression=expression.body,
            bound_names=scoped,
            registry=registry,
            diagnostics=diagnostics,
            location=location,
        )


def _scan_serialized_function_capture(
    *,
    capture_name: str,
    capture_spec: dict[str, Any],
    registry: PrimitiveRegistry,
    diagnostics: list[StaticDiagnostic],
    location: str,
) -> None:
    parameters = [str(item) for item in capture_spec.get("parameters", [])]
    captures = {
        str(name)
        for name in dict(capture_spec.get("captures", {})).keys()
    }
    nested = {
        str(name)
        for name in dict(capture_spec.get("functions", {})).keys()
    }
    bound_names = set(parameters)
    bound_names.update(captures)
    bound_names.update(nested)

    body = str(capture_spec.get("body", "")).strip()
    if body:
        try:
            parsed_body = parse_expression_content(body)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(
                StaticDiagnostic(
                    code="E_CLOSURE_PARSE",
                    message=(
                        f"Unable to parse captured function body '{capture_name}' "
                        f"for policy checks: {exc}"
                    ),
                    location=location,
                    symbol=capture_name,
                )
            )
        else:
            _scan_expression_for_effects(
                expression=parsed_body,
                bound_names=bound_names,
                registry=registry,
                diagnostics=diagnostics,
                location=location,
            )

    nested_functions = dict(capture_spec.get("functions", {}))
    for nested_name, nested_spec in nested_functions.items():
        if not isinstance(nested_spec, dict):
            continue
        _scan_serialized_function_capture(
            capture_name=str(nested_name),
            capture_spec=nested_spec,
            registry=registry,
            diagnostics=diagnostics,
            location=location,
        )


def validate_workplan_policy(
    workplan: Any,
    *,
    legacy: bool,
    serve_mode: bool,
    goal_scope: Iterable[str] | None = None,
) -> list[StaticDiagnostic]:
    """Return static diagnostics for policy violations in a reduced workplan."""
    diagnostics: list[StaticDiagnostic] = []
    registry: PrimitiveRegistry = workplan.registry
    nodes: dict[str, NodeSpec] = dict(workplan.nodes)
    scoped_node_ids = _resolve_node_scope(nodes, goal_scope)

    read_roots = resolve_serve_read_roots() if serve_mode else ()

    for node_id, node in nodes.items():
        if scoped_node_ids is not None and node_id not in scoped_node_ids:
            continue
        if node.kind != "primitive":
            continue

        operator_name = str(node.operator)
        spec = _resolve_spec(registry, operator_name)
        if spec is None:
            continue

        if not legacy and is_effectful_primitive(spec, operator_name):
            _append_effect_diagnostic(
                diagnostics,
                operator_name=operator_name,
                location=node_id,
            )

        if serve_mode and is_read_operator(operator_name):
            path_value = _constant_string_argument(nodes, node, 0)
            if path_value is None:
                continue
            diag = _read_root_error(
                operator_name=operator_name,
                path_text=path_value,
                roots=read_roots,
                location=node_id,
            )
            if diag is not None:
                diagnostics.append(diag)

    if legacy:
        return diagnostics

    for node_id, node in nodes.items():
        if scoped_node_ids is not None and node_id not in scoped_node_ids:
            continue
        if node.kind != "closure":
            continue

        body = str(node.attrs.get("body", "")).strip()
        if body:
            bound_names = {
                str(node.attrs.get("parameter", "")).strip(),
                *[str(name) for name in node.attrs.get("capture_names", [])],
                *[str(name) for name in dict(node.attrs.get("function_captures", {})).keys()],
            }
            bound_names.discard("")
            try:
                parsed_body = parse_expression_content(body)
            except Exception as exc:  # noqa: BLE001
                diagnostics.append(
                    StaticDiagnostic(
                        code="E_CLOSURE_PARSE",
                        message=f"Unable to parse closure body for policy checks: {exc}",
                        location=node_id,
                        symbol="closure",
                    )
                )
            else:
                _scan_expression_for_effects(
                    expression=parsed_body,
                    bound_names=bound_names,
                    registry=registry,
                    diagnostics=diagnostics,
                    location=node_id,
                )

        captures = dict(node.attrs.get("function_captures", {}))
        for capture_name, capture_spec in captures.items():
            if not isinstance(capture_spec, dict):
                continue
            _scan_serialized_function_capture(
                capture_name=str(capture_name),
                capture_spec=capture_spec,
                registry=registry,
                diagnostics=diagnostics,
                location=node_id,
            )

    return diagnostics


def enforce_workplan_policy_or_raise(
    workplan: Any,
    *,
    legacy: bool,
    serve_mode: bool,
    goal_scope: Iterable[str] | None = None,
) -> None:
    """Raise StaticPolicyError when policy checks fail."""
    diagnostics = validate_workplan_policy(
        workplan,
        legacy=legacy,
        serve_mode=serve_mode,
        goal_scope=goal_scope,
    )
    if diagnostics:
        raise StaticPolicyError(diagnostics)


def _resolve_node_scope(
    nodes: dict[str, NodeSpec],
    goal_scope: Iterable[str] | None,
) -> set[str] | None:
    if goal_scope is None:
        return None

    pending: list[str] = []
    reachable: set[str] = set()

    for node_id in goal_scope:
        node_key = str(node_id)
        if node_key in nodes and node_key not in reachable:
            pending.append(node_key)
            reachable.add(node_key)

    while pending:
        current = pending.pop()
        node = nodes.get(current)
        if node is None:
            continue

        dependencies = list(node.args)
        dependencies.extend(value_id for _name, value_id in node.kwargs)
        for dep_id in dependencies:
            dep_key = str(dep_id)
            if dep_key in nodes and dep_key not in reachable:
                pending.append(dep_key)
                reachable.add(dep_key)

    return reachable


def _runtime_policy_or_none() -> RuntimePolicyContext | None:
    return _RUNTIME_POLICY.get()


def _ensure_runtime_read_allowed(operator_name: str, source_path: str, roots: tuple[Path, ...]) -> None:
    diag = _read_root_error(
        operator_name=operator_name,
        path_text=source_path,
        roots=roots,
        location=None,
    )
    if diag is None:
        return
    raise ValueError(diag.message)


@contextmanager
def runtime_policy_scope(*, serve_mode: bool):
    """Apply runtime policy context for strict/dask execution."""
    context = RuntimePolicyContext(
        serve_mode=bool(serve_mode),
        allowed_read_roots=resolve_serve_read_roots() if serve_mode else (),
    )
    token = _RUNTIME_POLICY.set(context if context.serve_mode else None)
    try:
        yield context
    finally:
        _RUNTIME_POLICY.reset(token)


def enforce_runtime_read_path_policy(operator_name: str, args: Iterable[Any]) -> None:
    """Enforce read-root allowlist at runtime for serve mode."""
    context = _runtime_policy_or_none()
    if context is None or not context.serve_mode:
        return
    if not is_read_operator(operator_name):
        return

    args_list = list(args)
    if not args_list:
        return

    source = args_list[0]
    if isinstance(source, Path):
        _ensure_runtime_read_allowed(operator_name, str(source), context.allowed_read_roots)
        return

    if isinstance(source, str):
        source_text = source.strip()
        if not source_text:
            return
        _ensure_runtime_read_allowed(operator_name, source_text, context.allowed_read_roots)


__all__ = [
    "SERVE_DATA_DIR_ENV",
    "SERVE_EXTRA_READ_ROOTS_ENV",
    "StaticAnalysisError",
    "StaticDiagnostic",
    "StaticPolicyError",
    "diagnostics_from_exception",
    "diagnostics_payload",
    "enforce_runtime_read_path_policy",
    "enforce_workplan_policy_or_raise",
    "is_effectful_primitive",
    "is_read_operator",
    "resolve_serve_read_roots",
    "runtime_policy_scope",
    "validate_workplan_policy",
]
