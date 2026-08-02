# Runtime diagnostics

VoxLogicA2 presents runtime failures through structured diagnostics rather than
raw Python tracebacks. The engine and primitives produce typed context; only
the CLI and REPL render it.

## User contract

Normal failures are compact and actionable on stderr:

```text
error[E_IMAGE_NOT_FOUND]: Cannot open image

  --> analysis.imgql:12:21
12 | image = ReadImage("missing.nii.gz")
  |                     ^
Path does not exist: missing.nii.gz

Hint: Check the filename and dataset root.
Details: voxlogica errors show VLX-XXXXXXXX
```

Use `--error-details` (or `--debug`) to print the technical traceback for the
current failure. `voxlogica errors show VLX-XXXXXXXX` retrieves the stored
technical report. Reports are JSON, user-readable only, and live under
`~/.voxlogica/diagnostics` unless `VOXLOGICA_DIAGNOSTIC_DIR` overrides it.

`--error-format=json` provides a stable machine-readable diagnostic object.

## Architecture

`voxlogica.diagnostics` separates model, classification, rendering, and
storage. `Diagnostic` is JSON-serializable and contains only safe context;
`DiagnosticReport` additionally retains the exception chain and traceback.
Primitive and node wrappers preserve the original exception with `raise ...
from ...`; renderers never run inside worker threads.

Source provenance is a `SymbolicPlan.provenance` sidecar. It intentionally does
not enter `NodeSpec` or node hashes: source locations must not alter
content-addressed execution identity. A hash-consed node can retain several
source locations.

## Stable initial codes

| Code | Meaning |
|---|---|
| `E_IMAGE_NOT_FOUND` | `ReadImage` path does not exist |
| `E_IMAGE_UNREADABLE` | image exists but cannot be read |
| `E_PERMISSION_DENIED` | inaccessible input or output |
| `E_FILE_NOT_FOUND` | generic missing file |
| `E_INVALID_ARGUMENT` | invalid primitive argument or incompatible input |
| `E_IMAGE_MISMATCH` | image dimension/geometry/type incompatibility |
| `E_INDEX_OUT_OF_RANGE` | sequence or image index outside valid bounds |
| `E_EMPTY_SELECTION` | operation received an empty mask/selection |
| `E_STORAGE_FULL` | output or cache volume has no remaining space |
| `E_OUT_OF_MEMORY` | allocation failure |
| `E_INTERNAL` | unclassified implementation failure |

Future classifiers may add codes but must not reinterpret an existing code.
Never catch `KeyboardInterrupt`, `SystemExit`, or cancellation as diagnostics.
