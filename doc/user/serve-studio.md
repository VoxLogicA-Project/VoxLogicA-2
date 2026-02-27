# VoxLogicA Serve Studio

`./voxlogica serve` now exposes a multi-page studio at `/` with:

- Playground with async execution jobs
- Human-oriented side-by-side query/result lens (print-label selector + variable double-click)
- Results Explorer for persisted store artifacts (medical Niivue viewer, 2D image preview, structured values)
- Progressive markdown-driven example gallery
- Test/coverage/performance dashboard
- Storage/cache statistics dashboard

## Pages

1. **Playground**
- Submit programs asynchronously (`POST /api/v1/playground/jobs`)
- Poll status/results (`GET /api/v1/playground/jobs/{job_id}`)
- Kill stale/running computations (`DELETE /api/v1/playground/jobs/{job_id}`)
- Execution strategy is pinned to `dask` in serve mode.
- Always-on telemetry: wall time, CPU time, CPU utilization, Python heap peak, RSS delta
- Result lens:
  - selector built from print goals and declared variables
  - variable quick-inspection via editor double-click
  - per-query execution trace with `computed` vs `cached` node events
- Static diagnostics:
  - `/api/v1/playground/symbols` returns `available=false` with structured diagnostics when parse/static-policy checks fail
  - editor shows diagnostics inline and only overlays clickable variable tokens when symbol resolution succeeds
- Program library (fixed load directory only):
  - `GET /api/v1/playground/files`
  - `GET /api/v1/playground/files/{relative_path}`
  - directory controlled by `VOXLOGICA_SERVE_LOAD_DIR` (defaults to `tests/`)
- Serve read policy:
  - read primitives (`ReadImage`, `ReadTransform`, `load(path)`) are restricted to:
    - `VOXLOGICA_SERVE_DATA_DIR` (primary root)
    - `VOXLOGICA_SERVE_EXTRA_READ_ROOTS` (optional comma-separated roots)

2. **Results Explorer**
- Store-only inspection APIs:
  - `GET /api/v1/results/store`
  - `GET /api/v1/results/store/{node_id}`
  - `GET /api/v1/results/store/{node_id}/render/png`
  - `GET /api/v1/results/store/{node_id}/render/nii.gz`
- Renderers:
  - 3D medical volumes via Niivue (`.nii.gz` streamed from store payloads)
  - 2D images as PNG
  - compositional structured values (numbers, strings, arrays, mappings)
- Safety model:
  - no server-side save/export is allowed in serve mode
  - non-legacy runtime policy is always active in serve mode
  - inspection is limited to persisted results in the store

3. **Example Gallery**
- Source markdown: `doc/user/language-gallery.md`
- API: `GET /api/v1/docs/gallery`
- Examples are extracted from comment directives and executable `imgql` code fences

4. **Test Intelligence**
- API: `GET /api/v1/testing/report`
- Includes junit summary, coverage summary, and perf comparison report
- Performance chart endpoint: `GET /api/v1/testing/performance/chart`
- Primitive benchmark histogram endpoint: `GET /api/v1/testing/performance/primitive-chart`
- Interactive runs from UI:
  - `POST /api/v1/testing/jobs`
  - `GET /api/v1/testing/jobs/{job_id}`
  - `DELETE /api/v1/testing/jobs/{job_id}`
  - includes live logfile tail and run/kill controls
- Logs:
  - test jobs: `tests/reports/jobs/<job_id>.log`
  - playground jobs: `tests/reports/playground/<job_id>.log`

5. **Storage Stats**
- API: `GET /api/v1/storage/stats`
- Includes cached record counts, payload sizes, runtime-version distribution, and DB footprint

## Markdown Playground Directive

Use this exact pattern in markdown docs:

```markdown
<!-- vox:playground
id: unique-id
title: Human title
module: default
level: intro
strategy: strict
description: Short card description.
-->
```imgql
print "answer" 2 + 2
```
```

Supported keys:

- `id`: stable identifier
- `title`: gallery card title
- `module`: namespace tag (used for filtering)
- `level`: progression tag
- `strategy`: preserved as metadata; serve playground executes with `dask`
- `description`: card description

## Report Artifacts

By default `./tests/run-tests.sh` generates:

- `tests/reports/junit.xml`
- `tests/reports/coverage.xml`
- `tests/reports/perf/vox1_vs_vox2_perf.json`
- `tests/reports/perf/vox1_vs_vox2_perf.svg`

These files are consumed directly by the serve dashboards.
