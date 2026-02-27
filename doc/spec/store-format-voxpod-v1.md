# VoxLogicA Store Format Specification: `voxpod/1`

## 1. Scope
This document is normative for persisted value representation in VoxLogicA-2.

- Format identifier: `voxpod/1`
- Persistence model: JSON + Binary
- Execution strategy: dask-only

Old result payload formats are unsupported by design in this branch. If the on-disk schema/version does not match, the results DB is recreated destructively.

## 2. Canonical Descriptor Contract
Every inspectable value descriptor MUST provide:

- `vox_type` (required): one of
  - `null|boolean|integer|number|string|bytes|sequence|mapping|ndarray|image2d|volume3d|unavailable|error`
- `format_version` (required): `voxpod/1`
- `summary` (required): object with type-specific metadata
- `navigation` (required): object
  - `path` (string, normalized path)
  - `pageable` (boolean)
  - `can_descend` (boolean)
  - `default_page_size` (integer)
  - `max_page_size` (integer)
- `render` (optional): render hints (for example `image2d` or `medical-volume`)

## 3. Result Envelope (DB `results` row)
Persisted root result rows store:

- `status`: `materialized|failed`
- `format_version`: `voxpod/1`
- `vox_type`: root Vox type
- `descriptor_json`: canonical descriptor JSON
- `payload_json`: encoding-specific JSON payload
- `payload_bin`: optional binary payload
- `error`, `metadata_json`, runtime/version/timestamps

## 4. Binary Payload Layouts
### 4.1 `bytes`
- `vox_type=bytes`
- `payload_json.encoding=bytes-binary-v1`
- `payload_bin` is raw byte content

### 4.2 `ndarray`
- `vox_type=ndarray`
- `payload_json.encoding=ndarray-binary-v1`
- `payload_json` keys:
  - `dtype` (NumPy dtype string)
  - `shape` (integer array)
  - `order` (`C`)
  - `byte_order` (`little`)
- `payload_bin` is contiguous C-order array data

### 4.3 `image2d` / `volume3d`
- `payload_json.encoding=sitk-image-binary-v1`
- `payload_json.array`: ndarray header (`ndarray-binary-v1`)
- `payload_json.image_meta`:
  - `dimension`, `size`, `spacing`, `origin`, `direction`, `pixel_id`
- `payload_bin`: underlying array bytes

## 5. Paging and Path Navigation
Paths use `/`-separated tokens with JSON Pointer token escaping (`~0`, `~1`).

Sequence persistence under `voxpod/1` uses pointer-style pages:

- root payload uses `payload_json.encoding=sequence-node-refs-v1`
- each page item is a reference object carrying a deterministic child `node_id`
- child values are persisted as regular `results` rows under those child node ids
- page payloads do not inline large binary-capable values (for example 3D volumes)

Page APIs:

- `POST /api/v1/playground/value/page`
  - Request: `{program, node_id|variable, path, offset, limit, enqueue}`
  - Response includes:
    - `materialization`, `compute_status`, `job_id?`, `request_enqueued`
    - `descriptor`
    - `page: {offset, limit, items, next_offset, has_more, total?}`

- `GET /api/v1/results/store/{node_id}/page?path=&offset=&limit=`
  - Same page shape, sourced from persisted POD pages when available.

Sequence and mapping items in page responses MUST expose item descriptors using the canonical contract.

## 6. Error Semantics
Unsupported inspectable runtime values MUST surface:

- code: `E_UNSPECIFIED_VALUE_TYPE`
- clear message with node/path context

Unsupported values are compute-valid but persist-invalid:

- persistence is skipped
- warning metadata is attached
- inspect requests fail explicitly under spec endpoints

## 7. Compatibility Statement
`voxpod/1` is the only official store format in this branch.

- Backward read compatibility for legacy payload encodings is intentionally not provided.
- Store schema/version mismatch triggers destructive DB recreation at startup.
