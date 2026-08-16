# The workspace: store, document, actions, MCP

Audience: anyone building the real UI on top of the bento board.

**Status: built.** The store, the document and its `.imgql` round-trip, the
action manifest, the MCP server and its automatic registration with the MCP
clients on the machine, and -- as of the `results` sub-store described in
section 4 -- R6 as well: a card bound to a node shows that node's state and
changes as it changes. Section 9 tracks what is left, which is now viewers
rather than plumbing.

The board this sits on is [ui-bento.md](ui-bento.md); what is *inside* a card --
lenses, the source surface, viewers and the compute service -- is
[ui-cards.md](ui-cards.md); how a browser ends up connected at all is
[ui-architecture.md](ui-architecture.md); the visual rules are
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
  comments opens as a large code card holding every byte of it, beside one card
  for each output the program declares (`print`, `save`) -- derived, never
  written, so opening a file cannot modify it. Strict requirement.
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
results.get(hash) -> { hash,
                       state: "unknown" | "pending" | "computing" | "done" | "failed",
                       value?, valueType?, summary?, error?, at? }
```

`voxlogica/ui/results.py` on the server, `src/lib/store/results.svelte.ts` in the
browser. Two ways to read the same thing, because there are two kinds of reader:

- **Reactive**, for a card: reading it in a component re-renders that component
  when it changes. This is what makes a result card a live view of a hash rather
  than a snapshot someone has to refresh.
- **Awaited**, for an action, a test, or an agent:
  `await results.wait(hash, { state: "done", timeout })` in the browser, the
  `results.wait` action on the server and over MCP. One promise resolved from the
  same event stream, so there is no polling and no second source of truth. Always
  bounded: a wait with no bound is a hang with a friendlier name.

Subscription is per hash and driven by what is on the board: a card that shows a
hash subscribes it (`ResultSubscription.svelte`, where "on screen" is a
component's lifetime and the teardown is Svelte's), and the server pushes updates
for subscribed hashes only. What that buys is that the traffic is a function of
*how much is being looked at*, not of how big the run is -- a hundred-thousand
node plan with four cards on screen is four subscriptions. It does not buy
per-client filtering: the hub fans out to everyone, and pretending otherwise
would mean a client id in the protocol that nothing reads.

#### Two sources, and one of them is not the engine

A node whose value is already in the results store is `done` before anything
runs. That is the entire point of a content-addressed cache, and a cache hit
rendered as `unknown` until somebody recomputed it would be the UI lying about
the most useful thing the system does. So the store is asked first and the engine
second; the engine only ever has *more recent* news, never contradictory news,
because a hash is what its value is. States are ranked, so a `pending` arriving
after a `computing` -- the scheduler does not serialise its bookkeeping against
this module -- is dropped rather than making a card flicker backwards.

#### What the engine reports

`ComputationEngine(observe=...)` (`engine/core.py`), threaded through
`ExecutionEngine` and `EngineExecutionStrategy`, and attached by `voxlogica run`
when a UI is serving. It is a **spectator**: optional, not called at all when
absent -- these sites are in the scheduler's dispatch path -- and an observer that
raises is swallowed, because a UI that could abort a computation for the sake of
a card has it the wrong way round. Fused cone members are reported too; reporting
only the exit would leave every interior card at `pending` through the work that
produces it.

#### Names, and why the map travels with the text

A card names a *binding* -- `mask`, as typed -- and only the reducer can say
which node that compiled to. `reduce_program_with_bindings` answers that, and the
map rides along in the workspace snapshot as `nodes`, because it is a property of
the text and the text is what just changed. A document mid-edit does not parse
and yields an empty map, which is the normal case rather than an error. A
sixty-four-character hex string passes through as itself on both sides, so an
agent that already has a hash need not invent a name for it.

**A trap worth naming.** A state is spread into a message envelope whose own
`type` says it is a result, so the kind of a *value* is called `valueType`. It
was `type` once; the states arrived as messages of type `"number"`, nothing
handled them, cards never updated, and nothing anywhere said why.

**Open:** whether `wait` also resolves on a *value* predicate (`value > 0.9`) or
only on state transitions. State-only is what is implemented, via an `until`
predicate in the browser that nothing yet passes.

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
6. ~~**`results` and `results.wait`** (§4, R6) — per-hash subscriptions fed by
   the engine.~~
7. ~~Creating and deleting cards from the UI.~~
8. ~~Undo, moving a card between pages.~~
9. **Viewers for the values themselves**, and the card anatomy they sit in --
   designed in [ui-cards.md](ui-cards.md), which supersedes the sketch that
   follows. Every result renders through
   `ResultState` today: its state, and its value only when that value is a small
   scalar. An image, a volume, a table and a chart are four more rows in
   `src/lib/viewers/index.js`, keyed on `valueType`. That table is the extension
   point and it is deliberately nearly empty — inventing type names before there
   are values to have them would be inventing the wrong ones.
10. **Clickable names in code cards**: a name in the source as a handle on the
    node it defines. The binding map that makes this possible now exists (§4).

### TypeScript, honestly

The store, the actions and the capture code are `.ts` / `.svelte.ts`. The
components are still `.svelte` with untyped `<script>` blocks: they are being
converted as they are touched rather than in one rename, per §7. Type *checking*
is still not wired into the tests -- esbuild strips types without checking them,
so today TypeScript buys editor help and documentation, not enforcement. That is
worth stating plainly rather than implying a guarantee that is not there yet.

---

## Undo, and what counts as a change

Undo is a stack of whole documents, as text, kept beside the workspace. The text
*is* the document — export is concatenation and parsing is lossless — so a
snapshot cannot drift from what it claims to represent, which a log of inverse
operations can and eventually does. A board is small; a hundred of them is
nothing.

Only actions that change the document are pushed. Turning a page, zooming and
focusing are not edits: an undo stack that made you step back through everything
you had looked at is a stack nobody would use twice. Each `Action` says which it
is, so the rule is data rather than a list of exceptions someone has to
maintain — and `workspace.undo` itself is not an edit, or undo would undo undo.

An edit made after an undo clears the redo stack, because redoing then would
restore a document that never existed. Opening another file clears both: undoing
into the previous document would be a trap.

`canUndo`/`canRedo` ride along in the snapshot, so a browser and an agent see the
same availability.

## Saving

There is none, and that is the feature. A workspace is not a thing you save, any
more than a drawer is: the file on disk **is** the document, and an unsaved change
is only a change nobody has written down yet. `workspace.py` debounces the write,
because a drag is dozens of changes and the file is one, and flushes on shutdown.

This was an explicit ⌘S once, on the reasoning that the document is a program in
somebody's repository and rewriting it on every nudge puts noise in a diff they
did not ask for. That reasoning was right about the diff and wrong about the
remedy: the answer to "I do not want every nudge in my history" is git, which the
library's project-as-folder shape is built for, not a keystroke the user has to
remember or lose work to. `dirty` is still in the snapshot for anything that
wants to show it.

---

## Identity, and the name

A card has two of them, and they are different fields for one reason: a name is
prose and prose changes.

| Field | What it is |
|---|---|
| `id` | The reference. Generated (`c1`, `c2`, …), never shown, never edited. What one card names another by. |
| `title` | The name. Shown on the card, edited by double-clicking it, free to collide with another card's. |

So a card can be renamed without anything that points at it noticing, and two
cards may both be called `threshold` without either losing its identity. Files
written before the split have only an `id`, and it reads as the name, because
that is what it was.

**Titles are always written quoted**, with `\` and `"` backslash-escaped the way
every other string in this project escapes them:

```
//@card id=c2 title="He said \"no\" \\ then left" kind=note x=0 y=3 w=5 h=2
```

Always, not only when the value needs it: a field that is sometimes quoted
teaches every reader of the file the wrong rule, and someone will type a space
into a title within the hour. Reading is the inverse, so the round trip is the
contract and the escaping is a detail.

## Derived cards

A card that exists to show something another card produces records where it came
from:

```
//@card id=c3 title="y" kind=result x=0 y=0 node=y from=c1
```

`from` is an `id`, which is exactly why `id` is not the name: rename the source,
move it, retitle it, and everything derived from it still points at it. Made
from the source card's own menu ("New result from this"), or by an agent through
`board.deriveCard`.

The relationship is one-directional and named after what it is — a *derived*
card and its *source* — not after anything owning anything else.

## Viewers

A card does not draw itself. It says what it is, and a viewer is chosen for it
from its `kind` and, once a result card has a value, from the *type* of that
value — which is why the choice is a function of both (`lib/viewers/index.js`)
rather than a field on the card. A card bound to a number and a card bound to an
image are the same kind of card and are not the same view.

There is one viewer today, and it is deliberately provisional: a plain text
editor, no syntax colour, no completion, no gutter. Its job is to establish the
shape every other viewer will have — it renders what the card holds, it says
when it wants the keyboard, and it hands back text. Everything richer is a
different viewer, not a bigger one.

**Enter edits, with the cursor at the end.** A card you pressed Enter on is a
card you want to add to. `mod+Enter` keeps the edit, Escape abandons it, and
clicking away keeps it. The same viewer, on the same key, edits the whole
document in the file view (Tab) — editing the file and editing a card are the
same edit written the same way, because the layout lives in the file's own
comments.

---

## The library

A **project is a folder** and a **file is an `.imgql` in it**. That is the entire
model, and it is deliberately not a model at all: there is no index, no database
and no manifest, because each of those is a second description of the filesystem
that can disagree with it. Make a folder in Finder and it is a project; drop a
file in and it is in that project; put the folder in a repository and git has
ordinary files to track. Nothing has to be told.

**The sidebar is the tab bar.** One file shows in the pane at a time and the list
beside it is how you reach another, so there is no tab strip — a strip of tabs is
a second copy of that list, in a different order, truncated, and it is where
"which of these nine is the one I mean" comes from.

Files at the top of the library are **loose**, and that is the default
destination: where a new file goes when nobody has said where, and where it stays
until it is dragged into a project. "Unfiled" is a location, not a limbo.

| Gesture | Effect |
|---|---|
| Click a file | Open it. Whatever the last file still owed to disk is written first. |
| Drag a file onto a project | Move it into that folder. Onto the top: out of any project. |
| Alt-drag | Copy instead of move, as in every file manager. |
| ⌘/Ctrl+X, C, V | Cut, copy, paste — the whole selection, into the project you paste in. |
| Double-click a name | Rename the file, or the project. |
| `+` beside *Files* or a project | A new file there. |
| Click, ⌘/Ctrl-click, shift-click | Pick one, add one, pick a range. |
| Right-click a file | Rename, move to any project, delete — applied to everything picked. |
| Drag the sidebar's edge | Resize it. Double-click the edge restores the default; ⌘/Ctrl+B hides it. |

**A cut does not remove anything.** Nothing is gone until it lands: files marked
for a move are drawn dimmed until they are pasted or the buffer is dropped, which
is the only honest way to draw "on its way out, but still here". A cut is spent
when it lands; a copy can be pasted again, which is what a copy means everywhere
else. A paste with no destination named goes beside the file you are looking at,
and the top of the library is a destination like any other — it says *Paste* in
its own header while something is held.

An **empty project can be tidied away** from its own menu, with the same two
steps. Only an empty one: deleting a project that still holds files would be
deleting the files, which the list already does explicitly, one at a time. A
linked folder is not ours to delete — *Remove from the list* forgets it and
leaves it where it is.

**Deleting is two steps.** The menu arms it and an inline bar confirms it — not a
modal, because a dialogue that steals the keyboard to ask one question is worse
than the mistake it prevents, and this one can be ignored by carrying on. It is
the only action here that cannot be undone: undo covers the document, not the
filesystem.

The open file **follows what happens to it**: renamed, moved between projects, or
carried along when its project is renamed. An editor pointing at a path that no
longer exists would write the next change to a file nobody can find. Deleting the
open file opens nothing rather than leaving a ghost.

`workspace.moveTo` (the footer's *Move…*, which opens the system's own save
panel) is the different act: it takes a file **out of the library** entirely —
typically into a repository — and a folder that existed for that one file goes
with it. A project holding several files stays where it is; moving one of its
files must not empty a project somebody is still using.

---

## Which paths a client may name

Every instance binds loopback only, so the only client that can reach it is the
person sitting at the machine, and the rule is deliberately **empty**: no
restriction at all. A check that stopped somebody opening their own file would
protect nobody — they can open it with any other program they have — and it
would cost them the one thing a local tool is for.

The rule exists anyway, in one place (`guard.py`), because the day this listens
on anything but loopback the answer has to change in one edit rather than in
fifteen call sites. Off loopback:

- the boundary is the **launch directory and its subdirectories** — nothing
  above it, nothing beside it;
- paths are **resolved before being checked**, because a symlink pointing out of
  the tree is the oldest way through a check like this one;
- the **system dialogues are refused**: a file picker that opens on the *host* is
  not something a remote client gets to have;
- what widens the boundary is the **user choosing a folder** in one of those
  dialogues, never a client asking.

`bind_loopback` is where the mode is set, so the socket and the boundary are one
decision. The remote case is not reachable today and no port is opened for it.

## Projects from elsewhere

*Add folder…* shows a folder you already have — a repository, typically — as a
project. Nothing is moved or copied: its files are still exactly its files, read
fresh from disk, and removing it from the list leaves the folder alone. The list
of linked locations is the one thing the filesystem cannot tell us on its own, so
it is the one thing stored (`projects.json`, a list of paths and nothing else).
A linked folder is drawn with a dashed icon and keeps its own name.

---

## Cut and paste, for cards

**The cut buffer is the file format.** Copying cards produces .imgql text — the
directives and the bodies, exactly as they are written on disk — and that text is
what goes on the system clipboard. So a copied card can be pasted into an editor
and read, mailed to somebody, kept in a gist, or pasted back into any workspace
in any window, and there is no second serialisation format that can drift from
the first one. Pasting is `parse` plus `import_fragment`; the clipboard needs no
special-casing anywhere.

It follows that pasting *plain program text* from anywhere works too: text with
no directives is one code card holding it, which is the same rule that opens an
un-annotated file.

**Everything that could collide is renamed, never refused.**

| What | On paste |
|---|---|
| `id` | Minted fresh. An id is this document's way of naming a card; the incoming one means nothing here. |
| `title` | Kept. Titles are prose and may repeat — that is why they are not identity. |
| `let` bindings | Renamed only if this document already defines the name (`mask` → `mask2`), and every reference **within the pasted cards** is rewritten with it, including a result card's `node=`. |

The last row is the one that matters: a pasted group has to still compute what it
computed where it came from. A copy whose `node=` still pointed at the original's
binding would show the original's value and look like it had worked.

Cut is copy-then-remove and is a change to the document, so undo covers it. Copy
is not, so it does not.

---

## Order, and where it comes from

The board arranges cards in *space*, and space says nothing about the order a
program is read in. So the order is not something anybody arranges: it is
**derived**, and the file is written with every name defined before it is used.
Drag a card wherever you like; where its text lands is not your problem.

**It is derived from the language, not guessed at.** `analysis.py` imports
`voxlogica.parser` — the same front end the engine uses — and walks its AST:

- what a card **defines** is the `identifier` of each `Declaration`;
- what it **needs** is every identifier its expressions mention (an `ECall`,
  which is what a bare name parses to), minus everything bound inside: the
  declaration's own parameters, a local `let`, the variable of a `for` or a
  `filter`;
- a `print` or a `save` uses what it names, and defines nothing.

A regular expression could have found the `let` names. It could not have known
that `t` in `let y = let t = 2 in add(t, x)` is not a dependency, or that `i` in
`for i in xs` is not — and the day it was wrong, the UI would have been wrong
about what a program means while the engine was right. There is one front end,
so there is one answer.

Nothing here asks whether a name is a primitive. It does not need to: a card
depends on another card only if that card *defines* the name, so `threshold`
being built in simply means no card provides it.

**Cards that cannot be read are pinned.** Source that does not parse — half
typed, mid-edit — reports "unknown" rather than a guess, and keeps its position
while everything around it closes ranks. Reordering text nobody understood is
how an editor loses work.

**A cycle is reported, not enforced.** Two cards that need each other have no
order, and that is exactly the state somebody is in *while untangling them*, so
the document is written as it stands and the pair is named in the UI. Refusing to
save at that moment would be the worst possible time to be strict. Recursion,
therefore, is still impossible — but you find out by being told, not by having
your work refused.

**Duplicate definitions are a fact, not an error.** Two cards defining `mask` are
listed with both names while you decide which one you meant. Pasting already
avoids creating them.

The order is stable: cards that do not depend on each other keep the order they
had, so writing a file back is the smallest change that is still a correct
program rather than a reshuffle.
