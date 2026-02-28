# UI Editor Requirements And Testing Strategy

## Scope

This document defines requirements and test strategy for the custom Svelte editor used in the Playground UI.

## Functional Requirements

1. The editor must support plain-text editing for VoxLogicA programs with stable cursor behavior.
2. The editor must render diagnostics from backend/static analysis and visually mark affected lines.
3. The editor must expose clickable symbol links in-code for declared variables.
4. Clicking a symbol must emit a deterministic event payload containing the symbol token.
5. The editor must emit hover signals for symbol/operator context integration in the surrounding UI.
6. The editor must support autocomplete with:
   - keyboard trigger (`Ctrl+Space`/`Cmd+Space`)
   - keyboard navigation (up/down, enter/tab, escape)
   - pluggable completion provider for future typed completion.

## Non-Functional Requirements

1. Editor behavior must remain deterministic under frequent re-renders.
2. Component API must be framework-native Svelte (events + props) with no backend coupling.
3. Diagnostics and completion logic must be unit-testable outside component rendering.
4. UI test runtime must run headless in CI-compatible environments.

## Test Strategy

### 1. Utility Unit Tests (Fast, deterministic)

Target: `src/lib/utils/vox-editor.js`

- Parse diagnostic locations.
- Completion context extraction and completion application.
- Default completion filtering/ordering behavior.

Current coverage implemented in:

- `implementation/ui/src/lib/utils/vox-editor.test.js`

### 2. Component Tests (Interaction-level)

Target: `src/lib/components/editor/VoxCodeEditor.svelte`

- Clicking an in-code symbol dispatches symbol-click event.
- Autocomplete opens and applies completion through keyboard path.
- Diagnostics render line-level visual error state.

Current coverage implemented in:

- `implementation/ui/src/lib/components/editor/VoxCodeEditor.test.js`
- `implementation/ui/src/lib/components/editor/VoxCodeEditorEventHarness.svelte` (event harness)

### 3. Integration Tests (Next increment)

Target: `PlaygroundTab` with mocked API client.

Recommended assertions:

- symbol click triggers value inspection request path
- hover preview updates as backend states change (`cached`, `running`, `pending`)
- static diagnostics block run launch and display actionable message.

### 4. E2E Tests (Next increment)

Recommended framework: Playwright.

Key scenarios:

- Load default example, hover/click variables, inspect value tree.
- Execute program and verify focused-result transitions (`queued -> running -> computed`).
- Use autocomplete in real browser context and verify insertion and caret movement.

## Tooling Baseline

UI test tooling added:

- `vitest`
- `@testing-library/svelte`
- `@testing-library/jest-dom`
- `jsdom`

Configured in:

- `implementation/ui/vite.config.js`
- `implementation/ui/src/test/setup.js`
