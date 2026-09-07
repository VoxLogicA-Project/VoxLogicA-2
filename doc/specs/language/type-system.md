# VoxLogicA Type System

## Scope

This document defines the static types a VoxLogicA program can carry, the rules
primitives declare over them, and the analysis that assigns one to every value a
program asks for. It is a contract between three parties: primitive authors
(who declare rules), the CLI (which runs the analysis), and the checker itself.

Implementation: `implementation/python/voxlogica/analysis/`.

## Type checking is abstract execution

The checker walks the same reduced plan the engine executes, in the same
demand-driven order, with types in place of values:

| | engine | type checker |
|---|---|---|
| evaluates | `NodeId -> value` | `NodeId -> VoxType` |
| per-node work | the primitive's kernel | the primitive's `TypeRule` |
| memo | `NodeTable.values` | `TypeChecker.types` |
| loop bodies | `Expander.reduce_chunk` | `TypeChecker._apply_closure` |
| starts from | the plan's goals | the plan's goals |

Nothing is checked that the engine would not run: an unreferenced definition
produces no node and therefore no diagnostic. Running the checker before an
execution is meaningful precisely because the two traverse the same object.

### Loop bodies and typed holes

A `closure` node carries its body as *source text*; the body becomes nodes only
when the loop is unrolled. The engine unrolls it by binding the loop variable to
each element's constant node and re-reducing (`engine/expander.py`). The checker
does the same thing once, with the loop variable bound to a **typed hole** — a
synthetic node that has a type but no value, whose type is the sequence's
element type. Reduction then yields a real body sub-DAG that is typed like any
other node, which is why

```
for x in range(0,5) do [x, x]
```

types as `sequence(sequence(int))` rather than as an opaque result.

Reducing a body creates nodes. They are interned into a **private copy** of the
plan's node table: type-checking a plan must never change what that plan
executes.

Closure application is memoized on `(closure node, argument type)`.

Nesting is capped at 64 applications. This is a **stack** bound, not a
termination argument: the iterative pass that types the static DAG cannot cover
the two recursions each application opens (reducing the body's AST, then typing
the sub-DAG that reduction creates). Termination does not depend on the cap — a
closure body is fixed source text, so a program nests as deeply as it is
written, and a genuinely recursive user function is inlined by the reducer and
diverges there, before any type is asked for.

Reaching the cap costs precision, so it is reported as a `W_TYPE_DEPTH` warning
rather than applied silently: a result of `any` must not blur "nothing declares
this" into "the checker stopped looking". Warnings never fail a run — a limit of
the analysis is not a defect in the program.

Diagnostics raised inside a loop body are anchored at the **enclosing loop's**
source location. The body is re-parsed from text, so positions inside it count
from the start of the body rather than of the file; reporting them directly
would look like a file offset and point somewhere else entirely.

## The type lattice

```
any                                       -- top; compatible with everything
number      int | float                   -- int <= float <= number
bool  string  image
sequence(T)                               -- covariant in T
map(K, V)                                 -- covariant in K and V
{f1: T1, ...}                             -- record; width and depth subtyping
A -> B                                    -- closure; contravariant in A
```

`join(a, b)` is the least upper bound. Types with no common supertype below
`any` join to `any`: the lattice has no unions, so a static type never describes
a set of unrelated shapes.

## The analysis is gradual, not sound

A primitive that declares no `type_rule` yields `any`, and `any` is compatible
with everything in both directions. Consequences, which are the point:

- The checker reports only a mismatch it can **prove** from declared rules.
- Adding a rule can turn silence into a diagnostic; it can never turn a program
  that ran into one that is rejected.
- A clean type check is therefore not a guarantee of a successful run. It is a
  guarantee that no declared rule is violated.

This is what makes it safe to run the checker before *every* execution, which is
the default.

## Declaring a rule

A rule is the primitive's abstract transfer function — argument types in, result
type out — and may be any Python function, which is how polymorphism is
expressed without type variables or a constraint solver.

Namespaces that register kernels in bulk (`arrays`, `vox1`) use the decorator;
`register_specs` reads the attribute back off the kernel:

```python
from voxlogica.analysis.type_helpers import primitive_type, simple_type
from voxlogica.analysis.types import VoxFloat, VoxNumber

@primitive_type(simple_type([VoxNumber(), VoxNumber()], VoxFloat()))
def num_add(left: float, right: float) -> float:
    ...
```

Namespaces that write their own `PrimitiveSpec` pass `type_rule=` directly:

```python
PRIMITIVE_SPEC = PrimitiveSpec(
    name="sequence",
    ...
    type_rule=sequence_of_arguments(),
)
```

Helpers in `analysis/type_helpers.py`:

| helper | rule it builds |
|---|---|
| `simple_type(args, ret)` | one fixed signature, matched by subtyping |
| `signature_type(required, ret, optional=...)` | trailing optional arguments |
| `variadic_type(elem, ret)` | any number of arguments of one type |
| `sequence_of_arguments()` | `sequence(join of the argument types)` |
| `index_type()` | `(sequence, i) -> element` |
| `slice_type()` | windowing; the sequence type is preserved |
| `overloads([rule, ...])` | an overloaded primitive; see below |

### Overloaded primitives

`overloads` tries every alternative and **joins the results of all that match**,
rather than taking the first. Taking the first would be wrong in the one case
that matters: an argument of type `any` satisfies every alternative, so the
first would win by accident and the call would be given a precise result type
nobody proved. Joining keeps the answer as precise as the evidence allows. For
SimpleITK's overload sets, where every alternative returns an image, the join is
still exactly `image`.

A call is rejected only when no alternative matches, and the diagnostic lists
what each one wanted.

A rule rejects a call by raising `VoxTypeError`; the checker turns that into an
`E_TYPE` diagnostic located at the call site and carries on with `any`, so one
bad call does not hide the rest of the program.

## Derived rules

A rule does not have to be written by hand. `simpleitk` exposes ~350 SWIG
wrappers, which already carry their own signatures, so its rules are **derived**
at registration time (`primitives/simpleitk/_signatures.py`) from two
complementary sources:

1. **The SWIG docstring**, which opens with the C++ signature(s):

   ```
   Add(Image image1, Image image2) -> Image
   Add(Image image1, double constant) -> Image
   Add(double constant, Image image2) -> Image
   ```

   This covers 342 of 352 wrappers and is the *only* place the overload sets
   appear, because SWIG collapses them into one `*args` Python function.
   Multiple lines become an `overloads` rule.

2. **The Python signature**, for the handful SimpleITK annotates natively
   (`ReadImage`, `GetArrayFromImage`, `Resample`, ...) — almost exactly the ten
   the docstrings do not describe.

The name-to-type mapping is deliberately **directional**. In an argument
position every integer width maps to `number`, because the wrapper casts a float
argument to an int when the parameter wants one; demanding `int` there would
reject `f(image, 3)`, since every numeric literal in a VoxLogicA program is a
float. In a return position the same name maps to the precise type. `bool`
arguments map to `any` for the same reason: SWIG's typemap takes any object's
truth value, so a program passing `1` runs.

Enums, transforms, pixel-ID values and nested vectors map to `any`. Claiming a
type for an opaque object manufactures errors instead of finding them.

## Higher-order primitives

`for_loop`, `map`, `filter` and `fold` are typed by the checker itself rather
than by a `TypeRule`, because their result depends on applying a closure (or, for
`fold`, on the combiner recorded in the node's attributes) and not only on the
argument types:

- `for_loop(seq, f)`, `map(seq, f)` → `sequence(f applied at the element type)`
- `filter(seq, p)` → `sequence(element type)`; `p` is still applied, for its
  diagnostics
- `fold(op, seq)` → `bool` for `&&`/`||`, otherwise the element type joined with
  the seed's type when one is given

## Running it

```
voxlogica typecheck FILE [--json]   # abstract execution only; prints goal types
voxlogica run FILE                  # type-checks first (default)
voxlogica run FILE --no-typecheck   # skip the check
```

`typecheck` exits `0` when clean and `2` on a type error, matching the parser's
and reducer's static-error exit code. A failed check aborts `run` before any
kernel executes. `W_TYPE_DEPTH` warnings go to stderr in both commands and
change neither exit code.
