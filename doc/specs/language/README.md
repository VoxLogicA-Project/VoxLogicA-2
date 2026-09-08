# VoxLogicA Language Specs

This directory is the canonical contract surface for source-language syntax and semantics.

Current specs:

- [syntax.md](./syntax.md): concrete surface syntax accepted by the parser.
- [semantics.md](./semantics.md): execution-facing meaning of core expression forms and operators.
- [type-system.md](./type-system.md): static types, the rules primitives declare, and the abstract execution that checks them before a run.

Rules for language specs in this directory:

- Keep syntax and semantics separate so parser evolution does not blur execution behavior.
- Prefer additive changes with explicit backward-compatibility notes.
- When a syntax form lowers to an existing primitive or runtime construct, document that lowering.
- Update tests in the same change that alters a documented language rule.