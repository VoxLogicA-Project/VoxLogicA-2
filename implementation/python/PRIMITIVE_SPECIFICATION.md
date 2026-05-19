# VoxLogicA2 Primitive Specification

## Purpose

This document specifies how a primitive must be defined in the Python
implementation of VoxLogicA2 on the `dag-only-core` branch.

A primitive is the unit of executable functionality used by the reducer and the
execution engine. A primitive definition has two responsibilities:

1. Describe the primitive symbolically so it can appear in the DAG.
2. Provide a Python kernel that computes the primitive at runtime.

## Terminology

- `namespace`: A directory under `implementation/python/voxlogica/primitives/`.
- `primitive name`: The unqualified name of the primitive inside its namespace.
- `qualified name`: `<namespace>.<primitive name>`.
- `kernel`: The Python callable that implements runtime behavior.
- `planner`: The function that converts a symbolic primitive call into a DAG node.
- `spec`: The `PrimitiveSpec` object that declares the primitive contract.

## Directory Layout

A primitive namespace is a Python package located at:

```text
implementation/python/voxlogica/primitives/<namespace>/
```

A file-based primitive is usually defined in:

```text
implementation/python/voxlogica/primitives/<namespace>/<primitive_name>.py
```

For example:

```text
implementation/python/voxlogica/primitives/default/sequence.py
```

## Discovery Rules

Primitive loading is performed by `voxlogica.primitives.registry.PrimitiveRegistry`.

The registry discovers primitives as follows:

1. It scans every namespace directory under `voxlogica/primitives/`.
2. It imports each `.py` file in that namespace, except files whose names begin
   with `_`.
3. For each imported module, it accepts one of these contracts:
   - `PRIMITIVE_SPEC` plus `KERNEL` or `execute`
   - `build_primitive_spec()` returning `(PrimitiveSpec, kernel)`
   - legacy `execute` only
4. It also loads namespace-level primitives returned by
   `voxlogica.primitives.<namespace>.register_specs()`.

The preferred contract is:

- `PRIMITIVE_SPEC: PrimitiveSpec`
- `KERNEL: Callable[..., Any]`

## Required Primitive Contract

A modern primitive definition should export:

```python
KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(...)
```

The specification object is defined in
`implementation/python/voxlogica/primitives/api.py`.

### `PrimitiveSpec` fields

Each primitive spec must define:

- `name: str`
  The unqualified primitive name. It must not contain `.`.
- `namespace: str`
  The namespace where the primitive lives.
- `kind`
  One of:
  `scalar`, `sequence`, `tree`, `dataset`, `effect`, `overlay`
- `arity: AritySpec`
  The accepted number of arguments.
- `attrs_schema: dict[str, type | tuple[type, ...]]`
  Reserved for symbolic attributes. Use `{}` if the primitive has no attributes.
- `planner`
  A function that converts a symbolic `PrimitiveCall` into a `NodeSpec`.
- `kernel_name: str`
  The runtime identifier used by the registry. It must be unique.
- `description: str`
  A human-readable description.

Optional field:

- `is_legacy_adapter: bool`
  This is reserved for registry-generated legacy adapters and should normally
  remain `False`.

### Naming constraints

The registry enforces the following:

- `name` must not be empty.
- `name` must be unqualified.
- `namespace` must not be empty.
- `kernel_name` must not be empty.
- `kernel_name` must be unique across the process.
- `qualified_name` must be unique across the process.

## Arity Specification

Argument constraints are expressed with `AritySpec`.

Use:

```python
AritySpec.fixed(n)
```

for exactly `n` arguments, or:

```python
AritySpec.variadic(min_args=k)
```

for a variadic primitive with at least `k` arguments.

The reducer validates primitive arity before execution.

## Planner Requirements

The planner is responsible for the symbolic DAG representation.

In the common case, use:

```python
default_planner_factory("default.my_primitive", kind="scalar")
```

This produces a planner that creates a `NodeSpec` with:

- `kind="primitive"`
- `operator="default.my_primitive"`
- `args`, `kwargs`, and `attrs` copied from the symbolic call
- `output_kind` equal to the declared primitive kind

Use a custom planner only when the symbolic node must differ from the default
direct mapping.

## Kernel Requirements

The kernel is the runtime implementation of the primitive.

### Allowed signatures

The execution engine supports three styles:

1. Keyword-only via `**kwargs`
2. Positional via explicit parameters
3. Variadic via `*args` or `**kwargs`

Examples:

```python
def execute(left, right):
    return left + right
```

```python
def execute(**kwargs):
    return [value for _, value in sorted(kwargs.items())]
```

### Runtime argument mapping

At runtime:

- positional DAG arguments are passed in source order
- keyword DAG arguments are passed by name
- for `**kwargs` kernels, positional arguments are encoded as string keys:
  `"0"`, `"1"`, `"2"`, ...

This means a variadic primitive commonly reads inputs like:

```python
kwargs["0"]
kwargs["1"]
```

### Forbidden kernel parameters

The registry rejects non-legacy kernels whose signature depends on runtime
internals. The following parameter names are forbidden:

- `engine`
- `storage`
- `session`

Primitive kernels must behave as pure runtime functions over their inputs.

## Namespace-Level Registration

A namespace package may define additional primitives in
`voxlogica/primitives/<namespace>/__init__.py` using:

```python
def register_specs() -> dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    ...
```

This mechanism is appropriate when primitives are generated or grouped rather
than kept one-per-file.

If present, each returned entry must map:

```python
primitive_name -> (spec, kernel)
```

## Legacy Compatibility

Legacy primitives are still loadable if a module exposes only `execute`.
In that case the registry synthesizes a `PrimitiveSpec`.

This behavior exists only for compatibility. New primitives should not rely on
it.

## Recommended Authoring Pattern

The recommended pattern for a new primitive is:

```python
from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(left, right):
    return left + right


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="my_add",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.my_add", kind="scalar"),
    kernel_name="default.my_add",
    description="Add two scalar values",
)
```

## Worked Example

The following is a valid sequence primitive:

```python
from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    ordered = sorted(kwargs.items(), key=lambda item: int(item[0]))
    return [value for _index, value in ordered]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="sequence",
    namespace="default",
    kind="sequence",
    arity=AritySpec.variadic(0),
    attrs_schema={},
    planner=default_planner_factory("default.sequence", kind="sequence"),
    kernel_name="default.sequence",
    description="Construct a sequence from literal elements",
)
```

This primitive:

- belongs to the `default` namespace
- accepts any number of arguments
- appears in the DAG as a primitive node with operator `default.sequence`
- returns a runtime Python list

## Semantic Expectations

Unless a primitive is explicitly designed otherwise, a new primitive should be:

- deterministic for the same inputs
- side-effect free
- self-contained
- independent from server, storage, and UI layers

In the current DAG-only branch, primitives should be defined against the core
planner and strict execution runtime only.

## Checklist

Before adding a primitive, verify that:

1. The file is placed under a valid namespace package.
2. The module exports `PRIMITIVE_SPEC`.
3. The module exports `KERNEL` or `execute`.
4. `name`, `namespace`, and `kernel_name` are consistent.
5. `arity` matches the runtime kernel behavior.
6. The planner’s operator name matches the intended qualified runtime name.
7. The kernel does not require forbidden runtime-internal parameters.
8. The primitive returns a value compatible with its declared kind.
