# Stable UI Specs

This directory contains versioned, future-extensible contracts for UI boundaries that are intended to evolve additively rather than by ad hoc rewrites.

Current specs:

- [viewer-contracts.md](./viewer-contracts.md): incremental host and adapter contracts for leaf viewers and full-record viewers.
- [editor-surface-contract.md](./editor-surface-contract.md): stable token/event surface for the program editor.

Rules for specs in this directory:

- Prefer additive fields over behavioral rewrites.
- Keep host lifecycle and callback semantics explicit.
- Document fallback behavior when optional integrations are absent.
- Update these docs in the same change that alters the contract.