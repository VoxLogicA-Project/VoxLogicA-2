# VoxLogicA Semantics

## Scope

This document defines the execution-facing meaning of core source-language constructs.

## Value Categories

Core language expressions may evaluate to:

- scalar values such as numbers, booleans, and strings
- sequence values backed by native lists, tuples, ranges, lazy runtime sequences, or compatible runtime adapters
- images and overlays through imported primitives
- closures in deferred contexts such as `map` and `for`

## Array Literal Semantics

An array literal evaluates each element expression and constructs an ordered sequence value from the resulting items.

Current implementation behavior:

- top-level and eagerly materialized array literals commonly materialize as native Python lists
- lazy/runtime sequence infrastructure still treats those values as sequence-like values

This means array literals participate in:

- paging and inspection where adapted as runtime sequences
- index access
- slice access
- higher-order operations such as `map`

## Index Semantics

`xs[i]` resolves the element at position `i` using the existing `index` primitive semantics.

Operational notes:

- the index expression is evaluated before access
- index coercion follows the existing `index` primitive rules
- out-of-range behavior is defined by the underlying runtime/index primitive and should be treated as an error path

## Slice Semantics

Slice syntax lowers to the runtime `slice` primitive with optional bounds represented explicitly.

Meaning of forms:

- `xs[start:stop]`: items from `start` inclusive to `stop` exclusive
- `xs[:stop]`: items from the beginning to `stop` exclusive
- `xs[start:]`: items from `start` inclusive to the end
- `xs[:]`: a full shallow slice of the sequence

Current implementation behavior for bounds:

- omitted bounds are interpreted as unbounded on that side
- bounds are coerced to integers when provided
- negative bounds are not part of the stable language contract yet
- for current sequence primitives, lower bounds are clamped at zero
- when `stop <= start`, the result is empty

The runtime preserves lazy behavior for compatible lazy sequence sources where possible.

## Operator Semantics

### Scalar Comparison Operators

The plain operators below normalize to scalar comparison primitives:

- `==` -> `num_eq`
- `!=` -> `num_neq`
- `<` -> `num_lt`
- `<=` -> `num_leq`
- `>` -> `num_gt`
- `>=` -> `num_geq`

### Scalar Boolean Operators

The plain operators below normalize to scalar boolean compatibility primitives:

- `&&` -> `bool_and_scalar`
- `||` -> `bool_or_scalar`
- `!` -> `not_compat`

`not_compat` is intentionally compatibility-oriented:

- for scalar values it performs scalar boolean negation
- for image-like values it delegates to image negation behavior

This keeps plain `!` usable while preserving compatibility with older image-oriented operator conventions.

## Deferred Runtime Semantics

Expressions inside deferred closures, such as function bodies invoked through `map` or `for`, are reparsed and evaluated at runtime.

For that reason, any new core syntax form must be supported consistently in:

- parser AST construction
- reducer lowering
- static policy AST walking
- strict runtime expression evaluation

Array literals and slice expressions are part of that end-to-end contract.