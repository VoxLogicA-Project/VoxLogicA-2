# Example Gallery

Canonical source for VoxLogicA Serve Studio **Example Gallery** cards.

## Layout

```text
doc/gallery/
  manifest.json          # index: metadata + program paths
  programs/
    <module>/
      <id>.imgql         # runnable program for one card
```

- **One program file per example.** File name matches `id` in the manifest.
- **Module folders** group examples (`default`, `simpleitk`, `vox1`, `strings`, `arrays`, `nnunet`, `test`, `mixed`).
- **`manifest.json`** is the only registry. Add or edit examples here and add the matching `.imgql` file.

## Manifest entry

Each object in `examples`:

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable slug (matches program file base name) |
| `title` | yes | Gallery card title |
| `module` | yes | Filter tag / namespace label |
| `level` | yes | Progression tag (`intro`, `core`, `intermediate`, `advanced`, `expert`) |
| `strategy` | yes | Hint for execution (`strict`, `dask`); serve may override |
| `description` | yes | Short card blurb |
| `program` | yes | Path relative to `doc/gallery/`, e.g. `programs/default/intro-hello.imgql` |

Top-level `modules` must list every distinct `module` value (sorted).

## API shape (for UI backport)

Serve should expose `GET /api/v1/docs/gallery` using `voxlogica.gallery.load_gallery()`:

```json
{
  "available": true,
  "examples": [
    {
      "id": "intro-hello",
      "title": "Minimal expression",
      "module": "default",
      "level": "intro",
      "strategy": "strict",
      "description": "...",
      "program": "programs/default/intro-hello.imgql",
      "code": "answer = 1 + 2"
    }
  ],
  "modules": ["arrays", "default", "..."]
}
```

The UI (`GalleryTab`) reads `examples` and `modules`; each card needs `code` inlined in the response.

## Adding an example

1. Create `programs/<module>/<id>.imgql` with the program body only (no markdown fences).
2. Append an entry to `manifest.json` `examples`.
3. Add `<module>` to `modules` if it is new.
4. Run `tests/unit/test_gallery_loader.py` (or full unit suite).

## Related docs

- Narrative language guide (prose): `doc/user/language-gallery.md`
- Serve Studio gallery tab: `doc/user/serve-studio.md`
