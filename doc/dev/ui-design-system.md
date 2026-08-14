# The UI design system

Audience: anyone writing or reviewing a line of front-end code in
`implementation/ui/`.

This document states the rules, the reason each one exists, and what breaks when
it is ignored. Where a rule is machine-checkable it is checked — see
[Enforcement](#enforcement) — because a convention that only lives in a document
is a convention that decays.

---

## 1. The one-sentence version

> A component may only name semantic tokens, must ship its own gallery entry
> beside it, and the gallery is the library — not a set of examples of it.

Everything below follows from that sentence.

---

## 2. Layout of the source tree

```
implementation/ui/src/
├── app.css                  # delegates to the design layer; holds nothing itself
├── main.js                  # mounts App, and (dev only) the dev page
├── App.svelte               # the app page: empty by design (see §5)
└── lib/
    ├── design/              # the design layer — global, and the only global CSS
    │   ├── index.css        #   single entry point; import order is load-bearing
    │   ├── tokens.css       #   two tiers of tokens (§3)
    │   ├── reset.css        #   a deliberately small reset
    │   └── typography.css   #   type tokens + element defaults
    ├── components/          # the library (§4)
    │   ├── index.js         #   its public surface; callers import from here only
    │   └── <Name>/
    │       ├── <Name>.svelte
    │       └── <Name>.gallery.js   # the entry that makes it appear in the gallery
    ├── gallery/             # the dev-only dev page (§5) — absent in production
    ├── state.svelte.js      # the store: one $state singleton, no store library
    └── connection.js        # the WebSocket to the run's own server
```

Three boundaries, and they are the reason the tree looks like this:

| Boundary | Rule | What it buys |
|---|---|---|
| design → components | components consume tokens; the design layer knows nothing about components | a repaint of the palette is one file |
| components → app | the app imports from `components/index.js` and nowhere deeper | a component can be split, renamed or foldered without touching callers |
| components → gallery | the gallery is *generated* from components | the library cannot drift from its documentation |

---

## 3. Tokens: two tiers, and the wall between them

`design/tokens.css` declares two tiers and the discipline is the wall between
them.

**Tier 1 — primitives.** Raw ramps: `--gray-*`, `--blue-*`, `--red-*`,
`--green-*`. They exist so tier 2 has somewhere to point.
**A component may never name a primitive.**

**Tier 2 — semantic roles.** What a component is allowed to say:
`--color-text-muted`, `--color-surface-hover`, `--space-4`, `--radius-md`,
`--text-sm`, `--shadow-overlay`, `--motion-fast`, …

Dark mode redefines **only tier 2**. That is the whole payoff: a component that
names `--color-text-muted` is correct in both themes by construction, and one
that names `--gray-600` is light-mode-only by construction — silently, and only
visible to a reviewer who happens to switch themes. This is why the wall is
enforced by a test rather than trusted.

Colours are `oklch()`: perceptually uniform, so an evenly-spaced ramp *looks*
evenly spaced, and changing hue does not drag lightness with it.

Metrics are a 4px rhythm (`--space-1` … `--space-8`). Every gap, pad and inset
in the UI is one of them. Most of what makes an interface look composed rather
than assembled is that there are eight spacings and not forty.

**Never** write a raw colour, px, ms or easing curve in a component. If a value
you need does not exist, the fix is a new *role* in `tokens.css` — one edit,
visible to every component and to the palette page — not a literal in one
component, invisible to both.

---

## 4. The component library

Four components carry the interface: `Button`, `Toggle`, `ContextMenu`, `Card`.
A fifth has to earn its place by removing something.

`ContextMenu` opens on right-click (or Shift+F10) over the region it wraps, and
that is all it does. A menu-button variant existed briefly, for a dev menu that
turned out not to be wanted; it was deleted the moment it had no caller rather
than kept "for later". API with no users is not an asset — it is something that
has to keep working, keep being tested, and keep being read, for nobody.

That is a design position, not laziness. Every additional way to express
"clickable" is another thing for the interface to be inconsistent about, and
every component that exists must be maintained, themed, made accessible and kept
in the gallery forever.

### Authoring contract

A component in this library:

1. **Lives in its own folder** with its `.gallery.js` sibling.
2. **Names only tier-2 tokens** in its `<style>` block.
3. **Scopes all of its CSS.** Svelte scopes by default; `:global` needs a
   comment saying why (there is exactly one today, in the Debug section, and it
   is explained in place).
4. **Uses the right element.** `Button` is a real `<button>`; `Toggle` is
   `role="switch"` with `aria-checked`, not a checkbox, because a checkbox says
   "this will be true when you submit" and a switch says "this is true now".
5. **Takes props, not global state.** A library component never imports the
   store. It is given values and hands back events, which is what makes it
   mountable in the gallery from a plain object.
6. **Ships every state it supports** as a gallery variant — including the ugly
   ones: `disabled`, empty, overflowing, `danger`.

### The `.gallery.js` entry

```js
import Button from "./Button.svelte";

export default {
  name: "Button",                 // required
  summary: "…",                   // required — one sentence, what it is *for*
  component: Button,              // required — the real module, not a copy
  variants: [                     // required — non-empty
    { label: "accent", props: { tone: "accent" }, text: "Run" },
  ],
  axes: ["tone", "size", "disabled"],  // optional — the props worth varying
  layout: "stack",                     // optional — for full-width components
};
```

`registry.js` validates every entry at import time and throws on a malformed
one: a broken gallery entry must fail loudly in dev, not render a blank panel.

### Adding a component

1. `mkdir src/lib/components/<Name>/`, write `<Name>.svelte`.
2. Write `<Name>.gallery.js` with every state it supports.
3. Export it from `components/index.js`.
4. Reload. It is in the gallery — there is no list to update, because
   `virtual:gallery` is generated at build time by globbing `*.gallery.js`
   (see `build.mjs`).

---

## 5. The dev page

Everything that is *about* the software lives here, so that the application's own
page can hold only what a user of VoxLogicA came for. **The app page is empty on
purpose**, and stays empty until it has content of its own; the state of the
machinery — instance, run, event log — is a development instrument and is the
`Debug` section of this page, not the product's front page.

**The way in is one button.** Bottom-right of any dev run there is a `dev`
button, and it goes to the dev page — no menu in front of it, because the page
already has a nav and a menu would be a second copy of that nav to keep in step.
It is a library `Button`: the dev affordance is not allowed a private widget of
its own, or it would be the first component outside the system it documents.

Every section is a place, so every section has a URL:

| Section | URL | |
|---|---|---|
| Moodboard | `http://127.0.0.1:<port>/#dev/moodboard` | documentation |
| Palette | `…/#dev/palette` | documentation |
| Typography | `…/#dev/typography` | documentation |
| Components (the library) | `…/#dev/components` | documentation |
| Debug | `…/#dev/debug` | **live** — reads the store |

The four design sections would render identically in a dead process; `Debug` is
meaningless without a live connection. That is why it is set apart in the nav and
behind a separator in the menu: a different *kind* of page, not a more important
one. It is also the only section that touches `state.svelte.js`.

`#dev` alone opens the last section you looked at. `⌘.` / `Ctrl+.` toggles the
page and `Escape` closes it. A dev UI also prints the link on startup:

```
[voxlogica] UI at http://127.0.0.1:10001/
[voxlogica] dev page at http://127.0.0.1:10001/#dev (or press Cmd/Ctrl+.)
```

That is not decoration. A page reachable only by a keystroke cannot be sent to a
colleague, bookmarked, or found again after a reload — and the one surface that
documents the design system is the last place that should be a secret.

Three properties make it worth having:

**It is read-only.** It is a mirror held up to the design system, not a theme
editor. A panel that could override tokens at runtime would let the app look
right while the source stayed wrong — the single most expensive kind of design
bug. If a colour is wrong you edit `tokens.css` and the panel shows the result
on the next reload.

**It reads from the running stylesheet.** `gallery/tokens.js` walks the CSSOM
for the custom properties declared on `:root`. Not a hand-kept manifest: adding
a token makes it appear, renaming one makes it move, deleting one makes it
vanish, with no second edit anywhere. A manifest beside `tokens.css` would be
the one artefact in this system able to quietly disagree with it.

**It is the library, not a picture of it.** Each specimen mounts
`entry.component` — the same module its callers import — with
`entry.variants[i].props`. A specimen that looks wrong *is* a component that is
wrong. There is no second copy that can be right while the real one is broken.

**It does not exist in production.** `main.js` reaches the page through a dynamic
`import()` inside `if (__DEV__)`. `build.mjs` defines `__DEV__` to the literal
`false` for a production build, so esbuild drops the branch and with it the page,
its five sections, the registry and all of their CSS. Measured on the current
tree: dev bundle 1.5 MB (inline source map), production bundle 51 KB JS + 6 KB
CSS with no trace of it. The library itself is tree-shaken out along with it,
because the app page does not use a component yet — which is the correct outcome,
not a regression: nothing is shipped that nothing renders.

---

## 6. Enforcement

Conventions rot; tests do not.
`tests/unit/test_ui_design_system_discipline.py` asserts, over the real source
tree:

- no component names a tier-1 primitive;
- no component hard-codes a hex/rgb colour, a px length (outside the design
  layer), a raw `ms` duration, or a bare `cubic-bezier`;
- every component folder has a `.gallery.js`, and every `.gallery.js` is a
  well-formed entry naming the component beside it;
- every component exported from `index.js` has a gallery entry, and vice versa;
- `app.css` contains nothing but the design-layer import;
- the production bundle contains no gallery code (built once, grepped).

When one of these fails, the fix is in the component, not in the test. If you
believe a rule is wrong, change the rule in this document and in the test in the
same commit, with the reason — that is the only way a rule stays true.

---

## 7. Things deliberately not done

- **No CSS framework, no component library.** The whole design layer is 360
  lines of CSS that a reviewer can read in one sitting; a framework would be
  larger than the app and would own decisions this document is about.
- **No web fonts.** The bundle is served by the run's own process and must stay
  self-contained; a system stack renders at native hinting on every platform,
  which for dense UI text beats any downloaded face.
- **No store library.** Svelte 5 runes *are* the store (`state.svelte.js`):
  `$state` is a deep proxy, so mutating `app.run.summary.nodes` is observed.
  There is no subscribe/unsubscribe to get wrong.
- **No runtime theme switching.** `prefers-color-scheme` decides. A theme toggle
  is a preference to persist, sync and test in two states; the OS already
  answered the question.
