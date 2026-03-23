# VoxLogicA Syntax

## Scope

This document defines the accepted source-level syntax for the core VoxLogicA language as implemented by the current parser.

## Core Commands

Supported top-level commands:

- declaration: `let x = expr`
- declaration without `let`: `x = expr`
- function declaration: `let f(a,b) = expr`
- save: `save "name" expr`
- print: `print "name" expr`
- import: `import "namespace-or-file"`

## Core Expressions

Supported expression forms:

- numeric literals: `1`, `2.5`, `-3`
- boolean literals: `true`, `false`
- string literals: `"hello"`
- identifier reference: `x`
- function or primitive call: `f(a, b)`
- operator call: `.+.(a, b)` or similar operator identifiers in call position
- infix operator application: `a + b`, `c > d`, `left && right`
- prefix operator application: `!flag`, `-x`
- parenthesized expressions: `(expr)`
- let-expression: `let x = value in body`
- for-expression: `for item in seq do body`
- array literals: `[expr1, expr2, expr3]`
- index access: `xs[i]`
- slice access: `xs[start:stop]`, `xs[:stop]`, `xs[start:]`, `xs[:]`

## Array and Slice Syntax

### Array Literals

Array literals use square brackets and comma-separated expressions:

```imgql
xs = [1, 2, 3]
rows = [[1, 2], [3, 4 + 1]]
```

Each element is a full expression. Elements are evaluated in order.

### Index Access

Single-element access uses one expression inside brackets:

```imgql
value = xs[0]
item = rows[1][0]
```

### Slice Access

Slice access uses `:` inside brackets:

```imgql
mid = xs[1:4]
head = xs[:2]
tail = xs[3:]
all_items = xs[:]
```

Accepted slice forms:

- both bounds present: `[start:stop]`
- omitted start: `[:stop]`
- omitted stop: `[start:]`
- both omitted: `[:]`

Negative bounds are currently not specified as source-level behavior and should not be relied upon.

## Operator Surface

Plain scalar operators currently supported in surface syntax include:

- comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`
- scalar boolean operators: `!`, `&&`, `||`
- existing arithmetic operators such as `+`, `-`, `*`, `/`

Operator precedence currently follows the parser’s generic infix/prefix structure rather than a fully stratified precedence table. Programs should use parentheses where precedence matters.

## Lowering Notes

The current parser lowers some surface forms into common internal expression shapes:

- `xs[i]` lowers to an index call form.
- array literals lower to a sequence-construction expression.
- slice syntax lowers to a slice expression that is later reduced to the runtime slice primitive.

These lowering details are part of the implementation contract and may evolve additively if external behavior is preserved.