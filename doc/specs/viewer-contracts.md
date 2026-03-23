# Viewer Contracts

## Purpose

The UI viewer system uses stable host components plus incremental contracts so tabs can update viewer content without recreating viewer instances unless the viewer family actually changes.

## Host Lifecycle

`StartViewerHost.svelte` owns one DOM host and one adapter instance.

Stable rules:

- a contract has an `adapterKey`
- the host reuses the current adapter when `adapterKey` stays the same
- the host destroys and recreates the adapter only when `adapterKey` changes
- every adapter implements:
  - `update(contract)`
  - `destroy()`

This is the incremental boundary. Callers can update contracts freely while preserving the viewer shell and any compatible viewer-local state.

## Leaf Viewer Contract

Leaf viewers are built by `buildLeafViewerContract(...)` in `implementation/ui/src/lib/components/tabs/viewers/viewerContracts.js`.

Supported families:

- `scalar`
- `text`
- `image`
- `image-overlay`
- `medical`
- `array`
- `message`

Required stable fields:

- `adapterKey`
- `label`

Family-specific fields are additive. Unknown fields must be ignored by adapters.

## Record Viewer Contract

Full record viewers are built by `buildRecordViewerContract(...)`.

Required stable fields:

- `adapterKey = "record-viewer"`
- `label`
- `state`

Supported `state` values:

- `empty`
- `loading`
- `error`
- `record`

Optional fields:

- `record`: the current voxpod record payload when `state = record`
- `message`: loading or error message
- `onNavigate(path)`
- `fetchPage({ nodeId, path, offset, limit })`
- `onStatusClick(record)`
- `pageRefresh`: optional incremental page refresh request

`pageRefresh` fields:

- `nodeId`
- `path`
- `preserveRecord`

If `pageRefresh` is present and the underlying viewer supports `refreshPage(nodeId, path)`, the adapter must call it on the existing viewer instance. If `preserveRecord` is true, the adapter must avoid replacing the currently rendered record in the same update step.

## Fallback Behavior

If `window.VoxResultViewer.ResultViewer` is unavailable:

- the `record-viewer` adapter must still accept the same contract
- loading and error states render plain text
- record states render JSON
- unsupported capabilities such as page refresh become no-ops

This fallback behavior is part of the contract and not an implementation accident.

## Forward Compatibility

Future viewer families or fields should be added by:

- introducing a new `adapterKey`, or
- adding optional fields to an existing family

Avoid overloading existing fields with incompatible semantics.