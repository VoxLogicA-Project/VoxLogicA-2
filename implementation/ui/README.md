# VoxLogicA Studio UI (Svelte 5)

This workspace contains the Svelte 5 frontend source for the Serve Studio UI.

## Architecture

- `src/App.svelte`: app shell + tab orchestration.
- `src/lib/components/tabs/`: feature modules (playground, results, gallery, quality, storage).
- `src/lib/api/client.js`: centralized API client and endpoint helpers.
- `src/lib/utils/`: formatting, log parsing, token editor, and value-diagnostics helpers.

## Commands

```bash
cd implementation/ui
npm install
npm run build
```

Dev server only (expects backend at `http://127.0.0.1:8000` by default):

```bash
cd implementation/ui
npm run dev
```

Unified dev supervisor (recommended, from repo root):

```bash
./voxlogica dev
```

`npm run build` compiles the UI to:

- `implementation/python/voxlogica/static/app.js`
- `implementation/python/voxlogica/static/app.css`

The backend continues to serve `implementation/python/voxlogica/static/index.html` and external viewer scripts unchanged.
