# Writing ImgQL

ImgQL is VoxLogicA's language. There is no single book for it — the material is
split between a narrative guide, runnable programs, and three precise specs.
This page says which one answers which question, so you do not have to guess.

Read in this order the first time.

## 1. Start by running something

[**The example gallery**](../gallery/README.md) is a reading path, not a
reference: every program is self-contained, commented, and prints or saves
something, and they are ordered from a one-line expression to a complete study
(a BraTS threshold sweep that scores every case, reports the distribution of the
per-case best threshold, and exports the worst cases at three anatomical levels).

```bash
python -m voxlogica.main run doc/gallery/programs/default/intro-hello.imgql
```

[**The language guide**](language-gallery.md) walks the same programs in order
and explains what each one introduces — core syntax, lazy sequences, `let ...
in`, custom infix operators, loops. This is the closest thing to a tutorial.

## 2. Then the language itself, precisely

The three specs in [`doc/specs/language/`](../specs/language/README.md) are
contracts rather than tutorials: they are what the parser and the runtime are
held to, and they are updated in the same change as the behaviour.

| Question | Read |
|---|---|
| What syntax is accepted? | [syntax.md](../specs/language/syntax.md) |
| What does this form *mean* — `for`, `fold`, sequences, closures? | [semantics.md](../specs/language/semantics.md) |
| What types do the operators have, and what is checked before a run? | [type-system.md](../specs/language/type-system.md) |
| How do I write a parameter sweep idiomatically? | [parameter-sweeps.md](../specs/language/parameter-sweeps.md) |

The sweep page matters more than its length suggests: a threshold or parameter
sweep belongs **in ImgQL**, expressed with `for` and `argmax`, not in a Python
driver that calls VoxLogicA once per point. The engine shares every common
subexpression across the whole sweep; a Python loop cannot.

## 3. Which operators exist

- [`doc/dev/modules/primitives.md`](../dev/modules/primitives.md) — what is
  available, by module.
- [nnunet-namespace.md](nnunet-namespace.md) — the `nnunet.*` operators
  (training, prediction, model handles) and what they expect.
- Writing your own: [`doc/dev/primitives-api.md`](../dev/primitives-api.md) and
  [`doc/dev/implementing-new-operators.md`](../dev/implementing-new-operators.md).

## 4. Running and inspecting

| | |
|---|---|
| [cli-options.md](cli-options.md) | Every command-line flag, including the store, the engine selection and thread control |
| [manual.md](manual.md) | The workspace application — cards, views, what a run looks like while it runs. This is the **interface** manual, not the language one |
| [serve-studio.md](serve-studio.md) | Serving the UI without opening a window |
| [api-usage.md](api-usage.md) | Driving VoxLogicA from Python |
| [vscode-mcp-ui-inspector.md](vscode-mcp-ui-inspector.md) | The MCP server that inspects and drives the running UI |

## Conventions for programs in this repository

Two, and they are not stylistic:

- **Comment the program.** Every `.imgql` file in the gallery explains what it
  computes and why the parameters are what they are. An uncommented sweep is
  unreadable six weeks later, including by its author.
- **Print or save something.** A program with no visible output cannot be
  checked, and does not belong in the gallery.

## What is missing

Named so nobody looks for it: there is no complete operator reference with
per-operator examples, and no printable manual. The specs cover the language,
the gallery covers usage, and the gap between them is per-operator detail —
which today means reading the primitive's own docstring.
