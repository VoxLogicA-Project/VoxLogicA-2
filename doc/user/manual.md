# VoxLogicA: the manual

Everything the application does, in the order you meet it. If a feature is not
here, it does not exist yet — a test enforces that, so this page cannot fall
behind the program (see the end).

---

## Starting

| Command | What it does |
|---|---|
| `voxlogica` | Opens the workspace in its own window. |
| `voxlogica run program.imgql` | Runs a program, serving a UI beside it. |
| `voxlogica serve program.imgql` | The UI on a program, computing nothing until asked. |

The window is the operating system's own web view, with the application's own
icon in the Dock and in ⌘-Tab. If your machine has none, a
browser window is used instead — the same UI, a worse frame.
`VOXLOGICA_DEVTOOLS=1` opens it with the inspector showing;
`VOXLOGICA_NO_NATIVE_WINDOW=1` forces the browser.

Starting up reopens **the document you had open last**. With none — a first
run, or a file since deleted — nothing is opened and the window offers to make
one. No file is ever created that you did not ask for.

A new document lives in the platform's application-data folder until you say
otherwise. **Move…** in the footer opens the system's own panel to choose where
it should live instead — typically a repository — and moves it there
(`workspace.chooseLocation`). Chosen once, not asked every time you start.

---

## The file, the board, and the two views

A workspace **is** an `.imgql` program. The layout lives in comments, so the file
runs, diffs and commits like any other source, and nothing is stored beside it.

- **Tab** swaps between the *board* (cards) and the *document* (the whole file);
  which one you are looking at is workspace state, so an agent can see it and
  put you back (`view.show`).
- Opening a file that has never been arranged gives you a card holding the
  program, plus **one card for every `print` and `save`** it declares.
- Opening a file never modifies it. The layout comments appear on your first
  edit, not on your first look.

**There is no Save.** The file on disk is the document; changes are written
shortly after they stop arriving. `workspace.save` exists for scripts, and
**⌘Z / ⇧⌘Z** undo and redo.

---

## Cards

| | |
|---|---|
| **drag** | Move. Selected cards move together. |
| **drag an edge** | Resize, from any side or corner. |
| **drag empty cells** | Draw a new card at that size. |
| **the +** | New card on the cell you are pointing at. |
| **double-click** | Maximize into the free room; again to restore. |
| **double-click the name**, or **F2** | Rename in place. |
| **click the text** | Type. The caret lands where you clicked; there is nothing to open first. **⌘Enter** or clicking away keeps it, **Escape** abandons. |
| **long press** | Show this card alone; again to leave. |
| **⌘F** | The same, from the keyboard. |
| **⌘D** | Duplicate. **⌘R** makes a result card from the selection. |
| **⌘⌫** | Delete. |
| **⌘A** | Select every card on the page. |
| **arrows** / **⇧arrows** | Move / resize by one cell. |
| **⌘→ ⌘←** | Send to the next or previous page. |
| **⌘X ⌘C ⌘V** | Cut, copy, paste — as `.imgql` text, so it pastes anywhere. |
| **shift-click** | Add a card to the selection. |
| **⌘= ⌘- ⌘0** | Zoom the board in, out, back to normal. |

**Cards never share a cell.** A drop or a resize onto occupied cells snaps
back, and the refusal is in the document itself, so nothing — no gesture, no
agent — can produce a board whose arithmetic no longer works. A file that
arrived overlapping says so at the bottom of the window, with a **Move them
apart** button that fixes it (`board.untangle`). Dragging a card onto
a file or project in the sidebar **moves it into that file** (hold ⌥ to copy).

**Save this** and **Print this**, in a card's menu, declare what it is about as
an output: the `save` or `print` is written into the program, as its own card. A
button that wrote a file directly would be an effect with no record — this way a
diff shows it, a colleague reads it, and a headless run performs it.

A card is a **code** card, a **note**, or a view of an output — a **print**, a
**save**, or a **result** bound to any node.

**A card can show several pictures at once.** A `print` of an array is a stack:
`print "scan" [flair, gt, mask]` draws all three, back to front, and the card
grows a row per layer. The row's dot cycles its **colormap**, the slider sets its
**opacity**, and the ◉ switches the layer off — none of which recomputes
anything, because a layer's **style** is kept in the card's comment and never in
the expression. A layer that this case does not have, or that has not been computed
yet, says so and has nothing to switch.

**Drag a card by its body onto another card** and the two **merge**: what it
drew becomes a layer of what it landed on, keeping its colour, and it stops
being a card because it became a row. **Drag a row** up or down to **move** that
layer in front of or behind the others; **⤴ on a row** takes it back out as a
card of its own. Merging and splitting are exactly each other, so a drop you did
not mean is one click away from undone.

**A card can walk a sequence.** Give it an **index** — `index=i` in its
directive — and `print "scan" flairs[i]` grows chevrons, or numbered tabs when
the sequence is short. Clicking one **sets** that index, which is one line of
your program (`let i = 3`), so **every card that mentions `i` moves with it**.
That is all master and slave is here: there is no link to make or break, just a
name two cards happen to share. An index bound to arithmetic rather than to a
plain number is left alone — it is your work, not a place to record a click.

---

## Running, and what a card is about

**Run** is the ▶ on a card's own title bar. It computes what *that card* is
about; its dependencies follow on their own, and every card showing one of them
updates live — including cards you did not run.

A card that declares several names is about the **last** one. The selector in
its title bar chooses another; it appears only when there is a choice.

Values are addressed by content, so anything computed before — by an earlier
run, in another window, yesterday — is already `done` and shows at once.

A node reads as **not computed**, **queued**, **computing**, **done**, or
**failed**. An image or a volume is *drawn*: hover it to make it live, and drag
in it to move the crosshair. Only a handful of viewers can be live at once —
the rest keep their last frame until you approach them, because a browser has
about sixteen WebGL contexts to give and a board can hold more cards than that.

---

## What cards show — ⌘L

| | |
|---|---|
| **code** | The program. Every name it binds is underlined with the state of what it names. |
| **code + value** | The program, and what the focused name currently comes to. *(default)* |
| **value** | The value alone. |

**Select any part of a program** and the footer says what it is and whether this
machine has already worked it out. The selection is compiled *in that
document's context*, so `threshold(flair, 0.6)` means what it means there — and
if the same expression was computed yesterday under a different name, it is
already `computed`, because a value is its content (`results.hashOf`).

**⌘L** changes it for every card at once — the control at the bottom of the
window says which. **⇧⌘L** changes the selected card alone, cycling through the
three and back to following the board; its own menu lists the same choices
under **Shows:**.

---

## The file list — ⌘B

Projects are folders and files are files; one file is open at a time, so there
are no tabs. Drag files between projects, or use ⌘X / ⌘C / ⌘V.

| | |
|---|---|
| **⌘K** | Jump to the filter. Type `label:draft` to filter by label instead of by name. |
| **⌘U** | Sort by name, or by last changed. |
| **⌘N** | New file, where the open one lives. |
| **⇧⌘P** | New project. **⇧⌘O** shows a folder you already have. |
| **⌘E** | Reveal a file in the folder it is in. The footer's ⤢ does the same for the open workspace. |
| **⌘;** | Label the open file. The same label again takes it off, and so does *Remove* in its menu. |
| **F2** | Rename. **⌫** deletes, and asks once first. |

**Examples** is a project you did not add and cannot lose: the programs that
ship with VoxLogicA, ready to open and run. They are **read-only**, and the
footer says so while one is open — there is no Save here, so a file that took
your edit and lost it would be the worst answer. Move… saves a copy wherever
you like, and the copy is yours to change.

**Labels are written into the file**, on its `//@board` line. So a label
survives a `git mv`, a copy, or a colleague's mail — there is no index to fall
out of step. Projects have no labels: a folder has no document to keep them in.

---

## Help — ?

The full list of shortcuts, always generated from the same table the application
acts on.

---

## For agents

Everything above is available over **MCP**, under the same names: the server is
registered automatically with the MCP clients on your machine, as `voxlogica`.
An agent reads the workspace with `workspace_document`, `workspace_imgql`,
`workspace_grid`, `card_get` and `ui_screenshot`, does anything a person can do
through the actions of the same name (`board_addCard`, `card_run`, `view_setLens`,
`library_addLabel`, …), and can read this manual with `voxlogica_manual`.

Two worth knowing:

- `results_get` — what is known about a node right now.
- `results_wait` — block until a node is done, then return it. Bounded; it says
  so rather than hanging.

`VOXLOGICA_NO_MCP_REGISTER=1` switches the registration off. Everything is
loopback only.

---

## Keeping this page honest

`tests/unit/test_ui_manual_discipline.py` fails when an action or a shortcut
exists and is not mentioned here. Adding a feature therefore means adding a line
to this file — not because somebody remembers to, but because the build says so.
