# reducer.py - Symbolic Reducer

## Canonical Code
- `implementation/python/voxlogica/reducer.py`

## Responsibility
Transforms parsed AST programs into symbolic `WorkPlan`/`SymbolicPlan` definitions.

## Scope Rules
1. Reducer may build nodes/goals and lexical bindings.
2. Reducer must not execute primitives.
3. Reducer must not write runtime materialization/storage artifacts.

## Output Model
- `WorkPlan.nodes`: `NodeId -> NodeSpec`
- `WorkPlan.goals`: list of `GoalSpec`
- `WorkPlan.imported_namespaces`: deterministic namespace order for unqualified primitive resolution.

## Lowering
- Primitive calls -> `NodeSpec(kind="primitive")`
- Literals -> `NodeSpec(kind="constant")`
- `for` and `map` -> symbolic sequence nodes with closure node references
- `save` / `print` -> goals only
