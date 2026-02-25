# VoxLogicA Serve Studio

`./voxlogica serve` now exposes a multi-page studio at `/` with:

- Playground with async execution jobs
- Progressive markdown-driven example gallery
- Test/coverage/performance dashboard
- Storage/cache statistics dashboard

## Pages

1. **Playground**
- Submit programs asynchronously (`POST /api/v1/playground/jobs`)
- Poll status/results (`GET /api/v1/playground/jobs/{job_id}`)
- Kill stale/running computations (`DELETE /api/v1/playground/jobs/{job_id}`)
- Always-on telemetry: wall time, CPU time, CPU utilization, Python heap peak, RSS delta

2. **Example Gallery**
- Source markdown: `doc/user/language-gallery.md`
- API: `GET /api/v1/docs/gallery`
- Examples are extracted from comment directives and executable `imgql` code fences

3. **Test Intelligence**
- API: `GET /api/v1/testing/report`
- Includes junit summary, coverage summary, and perf comparison report
- Performance chart endpoint: `GET /api/v1/testing/performance/chart`
- Primitive benchmark histogram endpoint: `GET /api/v1/testing/performance/primitive-chart`
- Interactive runs from UI:
  - `POST /api/v1/testing/jobs`
  - `GET /api/v1/testing/jobs/{job_id}`
  - `DELETE /api/v1/testing/jobs/{job_id}`
  - includes live logfile tail and run/kill controls

4. **Storage Stats**
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
- `strategy`: default run strategy (`strict` or `dask`)
- `description`: card description

## Report Artifacts

By default `./tests/run-tests.sh` generates:

- `tests/reports/junit.xml`
- `tests/reports/coverage.xml`
- `tests/reports/perf/vox1_vs_vox2_perf.json`
- `tests/reports/perf/vox1_vs_vox2_perf.svg`

These files are consumed directly by the serve dashboards.
