# The workspace: store, document, actions, MCP

Audience: anyone building the real UI on top of the bento board.

**Status: built, except results.** The store, the document and its `.imgql`
round-trip, the action manifest, the MCP server and its automatic registration
with the MCP clients on the machine are all in the tree and tested. What is *not*
built is section 4's `results` sub-store -- cards can be bound to a node, but
nothing yet delivers that node's state or value. Section 9 tracks the rest.

The board this sits on is [ui-bento.md](ui-bento.md); how a browser ends up
connected at all is [ui-architecture.md](ui-architecture.md); the visual rules are
[ui-design-system.md](ui-design-system.md).

This document changes as the thing gets built. Where a decision is still open it
says so, rather than pretending.

---

## 1. What the workspace is

A bento board of cards. Each card holds one **polymorphic** thing:

- a piece of the program — clickable, so a name in the source is a handle on the
  node it defines;
- a **result**, addressed by node hash and rendered as either the node's *state*
  (unknown, computing, done, failed) or its *content* (a number, an image, a
  table), reactively: when the hash's state changes in the store, the card
  changes;
- a note.

One program, many views of it, arranged by the user. The board is the frame; this
document is everything behind it.

---

## 2. The hard requirements

Numbered so the rest of the document can point at them.

- **R1 — One store.** A single centralised Svelte store defines what is on the
  board and what is in every card. Not per-component state that happens to add
  up to a workspace.
- **R2 — The document is a sub-store**, and it round-trips to an `.imgql` file
  **without data loss**, using special comments. A file *without* special
  comments opens as a single large code card. Strict requirement.
- **R3 — Every mutation lives in a UI-less module.** All functions that change
  the store are Svelte modules with no markup (`.svelte.ts`, which exists for
  exactly this), organised in a hierarchical namespace whose shape is the shape
  of the UI and of the user's tasks — not the shape of the code.
- **R4 — An MCP server from day one**, able to execute those same actions and to
  see everything the user sees.
- **R5 — TypeScript.**
- **R6 — The store is reactive, including the ability to _wait_ for a hash's
  state to change.**

---

## 3. The decision that shapes everything: where the document lives

R4 forces this, and it is worth being explicit because everything downstream
depends on it.

> **The server owns the document. The browser holds a reactive replica.**

An MCP client is not a browser. If the document lived in browser memory then:

- with no tab open, MCP would see nothing — but `voxlogica serve` with no client
  is a normal state, and an agent working on a workspace is exactly the case
  where nobody is looking;
- two tabs would be two documents, and "what the user sees" would be ambiguous;
- every MCP action would have to be relayed into a tab and back, so the agent's
  reach would depend on a browser being alive.

With the document server-side, "MCP sees what the user sees" is true by
construction rather than by synchronisation effort, import/export is a file
operation in the process that owns files, and a reload is a re-read rather than a
loss.

### What that costs, and the honest reading of R3

The mutation happens in Python. The `.svelte.ts` action modules R3 asks for are
therefore a **typed façade**: no UI, one function per action, each sending an
intent over the existing WebSocket and applying the confirmed result to the local
reactive replica. The invariant R3 is really about — *no component mutates state;
every change has a name and lives in one place* — holds, and is testable.

The risk this introduces is two implementations of one vocabulary drifting apart.
The answer is in §5: the vocabulary is declared once, as data, and both sides are
checked against it.

**Rejected alternative.** Browser owns the document, server persists it. Simpler
until R4, then impossible without a browser in the loop.

**Not yet decided.** Whether actions are applied optimistically in the browser
before the server confirms. Over loopback a round trip is well under a frame, so
the plan is *no* optimism — the replica is a pure projection — and to revisit only
if a gesture ever feels attached to the network. Dragging is the one candidate:
it already runs locally and commits once, on release (§4 of ui-bento.md).

---

## 4. The store (R1, R6)

One store, sub-stores by lifetime — which is also what belongs in the file and
what does not:

```
workspace
├── document     the persisted thing: board geometry + cards        → the .imgql file
├── view         page, zoom, selection, what is focused             → never exported
├── results      hash → state/value, subscribed per visible card    → from the engine
└── connection   status, clientId, build errors                     → transport truth
```

`document` is the only sub-store that round-trips. `view` is deliberately
excluded: which page you were on is not a property of the program, and a diff
that changes because someone scrolled is a diff nobody wants to review.

### Results, and waiting for one (R6)

```
results.get(hash) -> { state: "unknown" | "pending" | "computing" | "done" | "failed",
                       value?, error?, at? }
```

Two ways to read the same thing, because there are two kinds of reader:

- **Reactive**, for a card: reading it in a component re-renders that component
  when it changes. This is what makes a result card a live view of a hash rather
  than a snapshot someone has to refresh.
- **Awaited**, for an action, a test, or an agent:
  `await results.wait(hash, { state: "done", timeout })`. One promise resolved
  from the same event stream, so there is no polling and no second source of
  truth.

Subscription is per hash and driven by what is on the board: a card that shows a
hash subscribes it, and the server pushes updates for subscribed hashes only.
Streaming the whole store to every client would make the cost of a large run a
function of how many tabs are open.

**Open:** whether `wait` also resolves on a *value* predicate (`value > 0.9`) or
only on state transitions. State-only is simpler and probably enough.

---

## 5. The action namespace (R3, R4)

**One vocabulary, declared once as data.** `ACTIONS` in
`voxlogica/ui/actions.py` is the single definition -- name, parameters, types, one
line of documentation. From it:

- Python builds the dispatch table **and** the MCP tool schemas;
- the TypeScript façade calls the same names, and
  `tests/unit/test_ui_workspace_actions.py` fails if either side has a name the
  other lacks. A generator was the alternative; a test that reads both files
  costs one file instead of a build step, and catches the same drift.

The shape follows the user's tasks, per R3 (all of these exist today):

| Namespace | Actions |
|---|---|
| `workspace` | `open`, `export`, `save` |
| `board` | `addCard`, `removeCard`, `moveCard`, `resizeCard`, `setPage` |
| `card` | `setTitle`, `setSource`, `setKind`, `setViewMode`, `bindNode` |
| `view` | `goToPage`, `setZoom`, `select` |

Files, all free of markup:

```
src/lib/store/     workspace.svelte.ts        the replica
src/lib/actions/   dispatch.svelte.ts         the one road to the server
                   index.ts                   the namespace
```

**The rule components live by:** read the store, call an action, never assign to
the store. That is mechanically checkable, and there will be a test for it in the
same spirit as the existing design-system discipline tests — those already fail
the build when a component invents a colour, and this is the same kind of rule.

`view` actions could have been kept in the browser -- nothing about zoom belongs
in a file -- but they go to the server like the rest, because "which page am I
looking at" is something an agent has to be able to read *and set* in order to be
looking at what the user is looking at (R4).

---

## 6. The document format (R2)

**The document is always a valid `.imgql` program.** Every piece of workspace
metadata is a comment, so a document can be run, diffed, and committed like any
other source file. That is the point of storing it in `.imgql` at all rather than
in a sidecar JSON nobody can read.

```imgql
//@board cols=9 rows=8
//@card kind=code id=segmentation x=0 y=0 w=5 h=4
let flair = load("case_001_flair.nii.gz")
let mask  = threshold(flair, 0.6)

//@card kind=result id=dice x=5 y=0 w=4 h=3 node=mask view=state
//@card kind=note id=todo x=0 y=4 w=5 h=2
// Threshold is hand-tuned; sweep it before trusting the number.
```

### The model that makes losslessness true rather than hoped for

A file is a list of **segments**, each stored verbatim:

```
preamble          exact text before the first directive
segment[i]        { directive: the exact line, body: the exact text until the next directive }
```

Export is concatenation. Import is a split on lines matching `^//@(board|card)\b`.
Nothing is reformatted, re-indented, or re-quoted, so:

- **`export(import(text))` is `text`, byte for byte**, for an unedited document.
  Not "semantically equivalent" — identical.
- Editing one card changes that card's body and nothing else, so a diff shows
  what the user did.
- A directive key the reader does not understand is preserved, so an older build
  opening a newer document loses nothing.

Directive lines are regenerated only for cards whose geometry actually changed,
with keys in a fixed order, so moving one card does not rewrite the file.

### A file with no directives (R2)

Opens as **one code card** holding the whole text, sized automatically. And it is
exported as the bytes it came in as: directives are written only once the user has
arranged something, so opening a plain program and closing it again cannot
silently annotate someone's source file.

### Cards that are not code

A result card and a note card carry no program text; they are a directive line
with no body. This is still lossless, and still runs.

**Open questions.**

- **How a result card names its node.** A name (`node=mask`) is what a human
  wrote and survives editing the program; a hash is exact but changes when the
  program changes and means nothing to a reader. Current thinking: store the
  name, resolve to a hash per run, show both. A card bound to a hash that the
  current program no longer produces has to say so rather than showing a stale
  number — this is the same class of problem as the predictor-handle bug in
  `d5e6da0`.
- **Whether `//@` is the right marker.** It must be a comment in `.imgql`, unlikely
  to collide with anything a user writes, and greppable. `//@` satisfies all
  three; nothing else has been evaluated.
- **Undo.** A stack of inverse actions, or document snapshots. Snapshots are
  trivially correct and, for a document this size, probably cheap enough.

---

## 7. TypeScript (R5)

**Verified on this toolchain** (probe built against the project's own
`node_modules`): `<script lang="ts">` and `.svelte.ts` modules using runes with
generics and interfaces compile with **no preprocessor** — esbuild strips the
types, the Svelte 5 compiler accepts them. So the migration is a rename plus
annotations, not a build-system change.

What that does *not* give: type **checking**. esbuild only strips. Checking is a
separate command (`svelte-check` / `tsc --noEmit`) and belongs in the test suite,
not in the serve path — the UI must keep building in one step while someone is
looking at it.

Order: types first where they earn the most (`document`, the action manifest, the
card model), then the rest as it is touched. A big-bang rename of working files
buys nothing.

---

## 8. The MCP server (R4)

In-process, mounted at `/mcp` on the port the UI already serves -- the same
argument as [ui-architecture.md](ui-architecture.md) §1 for the UI itself: a
second process means a lifecycle, an IPC protocol, and two places for one state
to live.

**Doing** — every action in the manifest is a tool. Not a curated subset: the
tool list is built by iterating `ACTIONS`, so an action added for the UI is an
action the agent has, on the same day.

**Seeing** — the normative list, all implemented:

| Tool | Answers |
|---|---|
| `workspace_document` | the whole document: board, every card with its kind, mode, geometry and contents, and the current view |
| `workspace_imgql` | the document as the file it would be saved as, byte for byte |
| `workspace_grid` | the lattice: columns, rows, cell pitch, and which cells each card occupies |
| `card_get` | one card: kind, mode, geometry, contents |
| `ui_screenshot` | a PNG of the board, of the page, or of **one card** by id |

### Screenshots

The server has no screen, so it asks a connected tab and the tab answers
(`src/lib/capture.ts`: the DOM into an SVG `foreignObject`, onto a canvas, out as
a PNG). The alternative -- rendering the board server-side from the document --
would be a *reconstruction*: it would show what the layout says, and the two
differ exactly when it matters, which is when something is wrong. With no browser
open the answer is "nobody is looking", which is information rather than a
picture of a workspace nobody can see.

### Finding it: registration and discovery

An agent must see the workspace without anyone editing JSON, so at boot each
instance does two things (`voxlogica/ui/registration.py`):

- **announces itself** in a state directory (pid, port, url, program), pruned by
  liveness, because a killed process never gets to tidy up;
- **registers `voxlogica`** with every MCP client already installed on the
  machine -- Claude Code, Claude Desktop, Cursor, Windsurf, Codex.

The registered command is `voxlogica mcp`, a stdio bridge, **not** a URL: the URL
would name a port that changes on the next run and would be wrong the moment that
instance exited. The bridge looks up the live instance every time it is asked
something, and says so plainly when there is none.

Registration is deliberately timid, and the tests are mostly about that: only
clients whose own file or directory already exists, only a missing entry, nothing
else in the file touched, a corrupt config left exactly as found, and any failure
logged and ignored -- a computation must never fail because a config was
unwritable. `VOXLOGICA_NO_MCP_REGISTER=1` switches it off.

**Loopback only**, like the UI. An MCP server that can drive a workspace is
exactly as sensitive as the UI it drives.

**Open:** whether an agent's actions are visibly attributed in the UI. A card that
moved because something else moved it should probably say so.

---

## 9. Build order

1. ~~The board: lattice, constraints, gestures~~ — [ui-bento.md](ui-bento.md).
2. ~~The document model and the `.imgql` round-trip~~, with losslessness as a
   test over real programs, including a file with no directives.
3. ~~The store, server-owned with the browser as replica~~, in TypeScript.
4. ~~The action manifest, the dispatch table, the TS façade, and the test that
   neither side can drift.~~
5. ~~The MCP server over the manifest, with screenshots, and registration with
   the clients on the machine.~~
6. **Polymorphic card content.** Code cards render their text today; clickable
   names, notes as prose, and results are not there yet.
7. **`results` and `results.wait`** (§4, R6) — per-hash subscriptions fed by the
   engine. This is the one hard requirement not yet met.
8. Creating and deleting cards from the UI: the actions exist, the affordances do
   not.
9. Undo (§6), moving a card between pages (ui-bento.md §7).

### TypeScript, honestly

The store, the actions and the capture code are `.ts` / `.svelte.ts`. The
components are still `.svelte` with untyped `<script>` blocks: they are being
converted as they are touched rather than in one rename, per §7. Type *checking*
is still not wired into the tests -- esbuild strips types without checking them,
so today TypeScript buys editor help and documentation, not enforcement. That is
worth stating plainly rather than implying a guarantee that is not there yet.
