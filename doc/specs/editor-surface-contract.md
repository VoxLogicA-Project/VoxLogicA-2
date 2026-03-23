# Editor Surface Contract

## Purpose

`VoxCodeEditor.svelte` exposes a tokenized editor surface whose rendered overlay, native textarea, and emitted events must remain aligned.

## Stable Surface

The editor owns:

- the authoritative text value
- a tokenized overlay model derived from `buildEditorDocument(...)`
- a native textarea used for selection, editing, and accessibility

The overlay is visual; the textarea remains the editing source of truth.

## Stable Token Kinds

The token model currently recognizes:

- `keyword`
- `identifier`
- `symbol`
- `number`
- `string`
- `comment`
- `space`
- `operator`

Token metadata is additive. Existing token kinds should keep their meaning.

## Stable Events

Current emitted editor events:

- `change`
- `input`
- `completionstate`
- `completionapply`
- `symbolclick`
- `symbolhover`
- `symbolleave`
- `operatorhover`
- `hoverleave`

Event payloads should evolve additively.

## Interaction Rule

Pointer-driven interactions on overlay-visible tokens must resolve against the tokenized document model rather than DOM-specific text layout assumptions, so behavior remains stable across overlay rendering changes.

## Forward Compatibility

Future token interactions should:

- reuse token metadata from the editor document model
- preserve textarea selection and input semantics
- expose additive event payloads rather than replacing existing ones