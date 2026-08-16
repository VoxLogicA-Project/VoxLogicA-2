# A card: one program, at three distances

Audience: anyone building card content, viewers, or the compute service behind
them.

The board is [ui-bento.md](ui-bento.md); the store, the document and the actions
are [ui-workspace.md](ui-workspace.md); the visual rules are
[ui-design-system.md](ui-design-system.md). This document is about what is
*inside* a card, and it exists because the answer turned out to be one idea
rather than a list of features.

---

## 1. The idea

**A program and its values are the same object, seen from different distances.**

Not two things the UI has to keep in step: one thing. Every expression in a
VoxLogicA program has a Merkle hash, that hash is the node id the engine
schedules, and it is the key the results store is addressed by. So the text on
screen *is* an index into the cache. `let mask = threshold(flair, 0.6)` is not a
line that will later produce a value — it is a name for a value that either
exists already or does not, and the difference is a lookup.

Everything below follows from taking that literally.

- A card does not have a "result mode" that must be switched on. It has a
  **lens**: how far back you are standing from the same thing (§4).
- Cards do not need wiring to each other. Card B depending on card A is not a
  relationship the UI maintains — B's dependencies *are* A's bindings, because
  both are the same hashes. Content addressing is the wiring (§3).
- Showing a value and asking for one are the same act. A card is a **demand**
  on the graph, and Run on its titlebar is how you make it (§5).

### What this is not

It is not "one of many interaction modes bolted onto a viewer". Viewing is the
first interaction; editing, deriving, sweeping and comparing are the next ones,
and they all key on the same identity. The architecture is the identity, not the
viewer.

---

## 2. Anatomy

```
Card                      the shell: chrome, title, run, lens control
├── SourceSurface         the program, with state written into the text
│   ├── SourceEditor      the overlay illusion (§6)
│   └── decorate()        pure: (text, bindings, states) → spans
└── ValueSurface          the value of the focused binding
    ├── viewerFor()       pure: (card, result) → which viewer
    └── ViewerHost        instance discipline and the WebGL pool (§7)
```

Four rules keep this modular rather than merely subdivided:

1. **Surfaces do not know about cards.** `SourceSurface` takes text, bindings
   and states; `ValueSurface` takes a result. Neither reads the workspace store.
   A surface that reached for the store could not be put in the gallery, and the
   gallery *is* the component library.
2. **Decoration is a pure function.** `decorate(text, bindings, states)` returns
   spans and touches no DOM. It is unit-testable without a browser, and the
   document view (TAB) reuses it unchanged — one highlighter, two surfaces.
3. **Viewer choice is a data table**, keyed on `valueType`. Adding an image
   viewer is a row, never a branch inside a component.
4. **The card owns focus, not the surfaces.** Which binding is being shown is
   one field, read by both surfaces, so they can never disagree about what the
   card is about.

---

## 3. Focus: which binding a card is about

A card holds a *fragment*, and a fragment may declare several bindings. The one
it is about is its **focus**.

Default: **the last binding in the fragment.** The last expression of a *file* is
arbitrary — it moves when someone appends a line. The last binding of a *card* is
not arbitrary at all: that boundary was drawn by hand, and a fragment reads as
scaffolding building toward its final name. Earlier bindings are the working; the
last one is the answer.

Switchable from a control in the header listing the fragment's bindings. Stored
on the card, so it survives the round trip, and it is what `//@card focus=` says.

### Cross-card dependency is not a feature

Run card B, and card A lights up as computing. Nothing in the UI arranges that:
B's fragment mentions `flair`, `flair` resolves to a hash, and that hash is the
same one A's binding resolves to. The engine schedules it because B needs it, the
observer reports it, and both cards are subscribed to it. Two cards showing one
hash are one subscription and one answer.

This is why `analysis.py`'s dependency ordering is about *text* (which card must
be written before which) and never about values. Values need no ordering: they
have identity.

---

## 4. Lenses

Three positions, one control, board-wide with a per-card override.

| Lens | What the card shows |
|---|---|
| `source` | the program, identifiers styled by the state of what they name |
| `both` | the program, plus the focused binding's value inline *(default)* |
| `value` | the viewer, full bleed |

Board-wide by default because a board where twenty cards each sit in a mode
somebody set once is a board you cannot read at a glance. Overridable per card
because a volume wants `value` while the code beside it wants `source`, and
forcing one answer on both would make the lens useless.

It is a continuum, not a set of modes: `source` and `value` are the ends of the
same movement, and `both` is the middle. Nothing is hidden at either end — a
value is never shown without a way back to the text that names it.

---

## 5. Run, and the compute service

**Run lives on a card's titlebar**, not on the window's. A run is a demand for
what *this* card is about; dependencies come along because they must, which is
the engine's business and not something the user should have to say.

New machinery, `voxlogica/ui/compute.py`, because until now the UI server has
never run anything: `voxlogica run` computes and the UI watches. Now the UI asks.

```
Run(card) → goals = the card's bindings, as node ids
          → demand set (union, deduplicated by hash)
          → runner thread: compile the document, execute those goals
          → observations land in Results (ui/results.py) as they happen
          → every subscribed card redraws, including cards nobody ran
```

Decisions worth defending:

- **One runner, a queue, never two engines.** The engine owns process-wide
  resources — a thread pool, a memory governor, a store handle. Two concurrent
  engines would contend for all three and the memory governor would be reasoning
  about half the picture. A Run while one is in flight joins the *next* pass.
- **Joining the next pass is nearly free**, and this is the payoff of content
  addressing: by the time the second pass runs, everything the first computed is
  a cache hit. The naive fear — "queueing makes the second Run slow" — is wrong
  by construction.
- **A demand set, not a run list.** Demands are hashes and hashes deduplicate,
  so pressing Run on five overlapping cards is one demand set, not five runs.
- **Nothing is cancelled by a new demand.** A run in flight is producing values
  that are, by definition, worth having: they are addressed by content, so no
  later edit can invalidate them. Cancellation exists for the user asking for it,
  not as a consequence of asking for something else.
- **A demand is a `value` goal, and only that.** The language already has the
  right kind: `value` materialises a node and does nothing else, where `print`
  and `save` are effects. A Run therefore computes what was asked for and does
  *not* fire the document's `save`s — writing files because somebody pressed Run
  on an unrelated card is not something a button can be allowed to mean.
- **`save` is not subsumed.** A card can show you a value; it cannot put it on
  disk. `print` becomes optional once any binding can be shown, but `save` is an
  *effect*. The card offers "save this", and the action writes a `save` into the
  program — the effect stays in the text, where it can be read, diffed and run
  headless.

### Opening a file

A document's `print` and `save` become cards, because those are the outputs the
author declared. Everything else that carries no card directive stays in the
file and is reachable through the document view (TAB) until there is a reason to
do more. This is the conservative half of a rule whose strong form — *nothing in
the program may be invisible on the board* — is worth revisiting once real files
have been opened in anger.

---

## 6. The source surface: an illusion that has to be perfect

Identifiers cannot be styled inside a `<textarea>`, and a `<textarea>` is the
only element that gets caret, selection, IME, undo and paste right for free. So:
a transparent textarea over a `<pre>` mirror that renders the same text with
spans. The user types into something invisible and reads something else, and the
two must be indistinguishable.

CodeMirror was the alternative and would have arrived sooner. It was declined for
a bundle a design system has to re-theme anyway, on a surface whose whole point
is that it renders our own vocabulary of state.

**The contract.** Both layers share one set of metrics, declared once and applied
to both, never to one:

- `font-family`, `font-size`, `line-height`, `letter-spacing`, `tab-size`
- `white-space: pre-wrap`, `overflow-wrap`, `word-break`
- `padding`, `border-width`, `box-sizing`

**The failure modes, which are all the same failure.** Any pixel of divergence
shows up as text that drifts from its own highlighting, and it drifts *more the
further down you read*, which is exactly where nobody is looking when they check.

- scroll must be synchronised on **both** axes, mirror following textarea
- the mirror never scrolls on its own and never shows a scrollbar
- a trailing newline needs a trailing space in the mirror, or the last line
  collapses and everything below it is off by a line height
- the textarea's own glyphs are hidden with `-webkit-text-fill-color:
  transparent`, not `color: transparent`, so the **selection** still paints
- the caret gets an explicit colour: it is the one part of the textarea that must
  remain visible
- browser zoom and a changed root font size move both layers or neither

**What is written into the text.** A binding's name carries the state of what it
names — not computed, queued, computing, done, failed — as weight and colour from
the tokens, never as a badge beside it. A badge is a second thing to read; the
name is already there and already the thing you are asking about.

**Selecting a sub-expression asks the store about it.** The selection is resolved
to a hash and its state comes back, so highlighting three words answers "is this
already computed?". Two rules make it safe:

- **One hasher, on the server, always.** A JavaScript reimplementation of the
  Merkle hash would drift from the Python one and then answer cache questions
  *wrongly and silently*, which is worse than not answering them. The action
  takes a document and a range, never bare text — a sub-expression's hash depends
  on its environment, on what `flair` means at that point.
- **Debounced and memoised** on `(document, range)`, because a selection changes
  as fast as a pointer moves.

---

## 7. Viewers, and sixteen contexts

`viewerFor(card, result)` is a table keyed on `valueType`, with `ResultState` as
the honest default: for a 240×240×155 volume, "done, float32" *is* the useful
view until somebody asks for more.

For images and volumes, more is `Volume.svelte`, a Svelte component wrapping
**NiiVue**. NiiVue does WebGL, colormaps, orientation and crosshairs; we do the
reactive surface, the layer discipline and the context budget. An earlier
attempt (`viewerAdapters.js`, removed in `57506e0`) is worth reading for one idea
and no code: `adapterKey` — the instance is recreated only when the viewer
*family* changes, never for new data. On WebGL that distinction is the difference
between smooth and unusable.

**Layers are props, and props are declarative.**

```
layers = [{ hash, colormap, opacity, visible }, …]
```

Add, remove, reorder and restyle by changing the array. The component diffs it:
reordering must not reload a volume, and restyling must not touch the DOM. The
temptation is an imperative API that mirrors NiiVue's own; it would put the truth
in two places and the second one would be wrong within a week.

**A browser gives roughly sixteen WebGL contexts, and a board can hold more cards
than that.** So contexts are not owned by cards: there is a **pool of live
contexts** (small, single digits), lent by LRU to the cards that are visible,
hovered or focused. A card that loses its context keeps its last frame as a
bitmap and asks for one back on hover. The limit is respected by construction
rather than by hoping fewer than sixteen are open, and the failure it prevents —
a context silently lost, taking a card's picture with it — is one nobody would
diagnose from the symptom.

---

## 8. Build order, and where it has got to

1. ~~The compute service and per-card Run (§5)~~ — **done**. `ui/compute.py`,
   the `card.run` action, the Run button on the card header, and `running`
   derived in `App.svelte` from the results store rather than held as a flag.
   Verified end to end: Run on the program card computes `s`, and a `let`
   nothing prints can be demanded on its own.
2. `decorate()` and the source surface (§6), state written into names.
3. Lenses (§4).
4. Selection → hash (§6), the editor as a probe into the store.
5. `Volume.svelte` and the context pool (§7).
6. Focus control and the `save this` action (§3, §5).

Each of these is useful alone, which is the test of whether the split is real.

### Running the tests, and a trap that is worth knowing about

```bash
.venv/bin/python -m pytest tests/unit -k ui -q
```

Under four seconds, the whole set. If it takes minutes instead, **count the
`voxlogica` processes you have running**: every dev instance carries a source
watcher, and several of them rebuilding into the shared bundle cache
(`~/.cache/voxlogica/ui-bundles/`) while the suite asks for a build of its own is
enough to make the run look hung. It is contention, not a slow test, and the
suite is not the thing to go looking inside.

The real-engine tests in `test_ui_compute.py` call `_execute_with_engine`
directly rather than going through the runner thread. That is a deliberate
narrowing -- the threading properties are covered above them with an injected
execute -- and not a workaround for anything known to be broken.

**Still owed from the earlier audit**: labels on library files -- in the
document's own `//@board` line, per the reasoning that a label belongs to the
file that carries it (projects get none, they are folders) -- and a keyboard
shortcut for every action that is currently context-menu only.
