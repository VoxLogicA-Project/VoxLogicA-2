# The bento board

Audience: anyone changing `implementation/ui/src/lib/components/Bento/`.

The board is the frame the workspace is built in: cards that hold a program, a
node's state, a result. This document is the board itself — its geometry, its
model, and what it refuses to do. What goes *inside* the cards is
[ui-workspace.md](ui-workspace.md).

Living document. Section 7 is the list of things not yet decided.

---

## 1. The unit of measure

**Integer cell coordinates in the model. One CSS length for the cell, in rem.**

```
--bento-pitch: 4rem;              /* cell + gutter = 64px at default text size */
--bento-gutter: var(--space-2);   /* 8px, so the cell itself is 56px */
```

A card is `{ x, y, w, h }` in cells. A card `w` cells wide occupies `w` tracks
and the `w - 1` gutters between them, which is exactly `w * pitch - gutter`. The
board never stores a pixel.

### Why cells and not pixels

Three properties follow from the lattice, and none of them survives free pixel
placement:

- **A saved layout means the same thing on another screen.** `{x: 4, w: 5}` is
  the same arrangement on a laptop and on a 4K monitor. Free placement saves a
  layout that is correct on the machine that made it.
- **Two cards of equal width are equal.** Not "within three pixels". A wall of
  cards reads as composed because the sizes are drawn from a small set, and that
  is a property of the unit, not of the care taken while dragging.
- **"Is this spot free" is decidable.** Occupancy is integer interval overlap.
  With pixels it is a hit test with a tolerance, and a tolerance is a parameter
  nobody can choose correctly.

### Why rem and not px

The board scales with the reader's text size. A user who runs the browser at
20px base gets a proportionally larger board instead of stamp-sized cards beside
large type. Zoom is then a single multiplier on the same variable
(`--pitch: calc(var(--bento-pitch) * var(--zoom))`) rather than a second
coordinate system.

### Why 4rem

The cell has to be small enough to place things precisely and large enough that
placing them is not pixel-fiddling. Dashboards that solve the same problem land
in the same region: Grafana is 24 columns × 30px rows, `gridstack` defaults to
12 columns, `react-grid-layout` examples use 12 × 30px. At 64px the smallest
useful card is 2×2 (120×120px), a comfortable one is 5×4 (312×248px), and a
9-column board is 568px — a column of a normal window.

The pitch is a multiple of the 4px spacing rhythm the rest of the design system
uses, so a card edge lands where a padding edge would.

### The alternatives, and why not

| Alternative | Used by | Why not here |
|---|---|---|
| Fluid columns (`1fr` tracks, N columns scaled to the container) | Grafana, react-grid-layout, every responsive dashboard | The board must extend past the viewport, and a fluid column has no answer for that except reflow. A card 4 cells wide would also be a different size on every window, so "the same size" would stop being a thing two cards can be. |
| Free pixel placement + snapping | Figma, Miro | Snapping to what? Either there is a lattice (this design, with the lattice hidden) or there is not (and the three properties above are gone). |
| `repeat(auto-fill, minmax(...))` | CSS-native card walls | No placement at all: the browser decides where things go. The user cannot say "this card, here". |
| Fixed px cell | ad-hoc grids | Ignores text size. Correct until someone zooms. |

**Measured, not derived.** JS reads the pitch back out of the resolved
`grid-template-columns` rather than recomputing it from the tokens. Two sources
for one number is two chances to disagree, and the drag arithmetic has to land on
the cells the grid actually drew.

---

## 2. The model

```js
{ id, x, y, w, h,
  page = 0,        // which page of the board this card is on
  auto = false,    // size follows content until the user resizes it
  title,
  minW, minH, maxW, maxH,   // cells
  aspect }                  // w / h, width leads
```

`Bento` takes `cards` as a **seed** and owns the layout from then on, reporting
every committed change through `onchange(cards)`. It is not a two-way binding,
and that is deliberate: dragging a card means mutating it, mutation is only
visible if the object is a `$state` proxy, and requiring every caller — including
a gallery entry, which is plain JSON in a plain module — to have made one is a
rule that gets broken silently, by a board that moves nothing.

### One authority for placement

**Cards never share a cell**, and that is an invariant rather than a habit:
everything else here assumes it. Placement is *refused* rather than resolved, the
arithmetic that makes room for a drag begins by assuming nothing overlaps yet,
and the moment two cards do share a cell every later gesture behaves
inexplicably -- cards refuse to move anywhere, the avoidance stops working, and
nothing on screen says why.

Keeping an invariant true needs one place that decides. This is that place:

> **The document decides where a card may be. The browser only proposes.**

`voxlogica/ui/document.py` owns the algebra -- can this rectangle go here, who
must step aside, what does the board become -- and `voxlogica/ui/analysis.py`
owns the predicate the algebra is built from. The board previews a gesture
locally so it stays attached to the pointer, but the preview is a *drawing*, and
when the document refuses it the card snaps back.

Three rules follow, and each of them is a bug that happened:

- **Every card has a size in the document.** `auto` is a policy for the first
  measurement, not a permanent state: what the browser measures is written back
  like any other size. A card whose footprint exists only at render time is one
  the document cannot reason about, so it was invisible to the invariant and
  anything could be placed on top of it.
- **A rendering filter must never reach the geometry.** Focus narrows what is
  *drawn* to one card. It once narrowed the list `canPlace` consulted as well, so
  maximizing a focused card found an empty board and grew over everything on it.
  Two names now, `visible` and `occupants`, and a test that keeps them apart.
- **One gesture is one arrangement**, checked as a whole and applied
  all-or-nothing. Applied card by card it passes through states that genuinely
  overlap -- grow a card before its neighbour has moved -- so a per-step check
  refuses the resize and lets the neighbour move anyway, which is exactly how
  cards "avoided and then came back on top of each other". It is also what makes
  swapping two cards in one gesture possible at all.

**Rejected: enforcing this in the board alone.** It was, and it was not an
invariant -- a rule that lives in a component is a rule an agent over MCP walks
straight past, and half the ways to overlap did not go through that component
anyway.

#### The preview, and how it ends

The board keeps its own copy of the algebra, and that is not a contradiction of
the rule above: a drag is answered every frame under the pointer, and a network
round trip per frame is not a thing that can be made to feel right. So the board
*predicts*, draws the prediction, and holds it for the length of one round trip
so the card does not snap home and then jump to where it was dropped.

What matters is how the prediction ends. It used to end by **timing out**: if no
new layout arrived within 900ms, the drop was assumed refused. A guess about a
refusal, on top of a guess about the placement, and both were wrong in practice
-- a slow answer discarded a placement that had in fact been applied, producing
exactly the flash the preview exists to prevent, while a prompt refusal still sat
on screen for the rest of the grace period showing a layout that was not real.

The document answers. `board.arrange` returns whether it applied the
arrangement, so nothing has to be inferred:

| what comes back | what it means | what the board does |
|---|---|---|
| `ok: true, result: true` | applied | hold the preview until the layout echoes it |
| `ok: true, result: false` | considered and declined | drop the preview at once; the card snaps back |
| `ok: false` | the message never arrived | leave it to the timer |

Every optimistic placement goes through one function (`commitArrangement`), so
the prediction meets the answer in exactly one place and no caller has to
remember to check. The timer that remains is not a verdict on anything -- it is
only the end of waiting for an answer that is never coming, and it is generous,
because a preview held a moment too long is a far smaller wrong than one taken
back while the answer was still in flight.

Note that a refusal is `ok: true`. Being told "no" is not an error: dropping a
card where one does not fit is an ordinary thing to do, and it is the board's
job to show that it did not take, not to report a fault.

---

## 3. Auto-sizing

A card with `auto: true` has no width or height of its own: it measures what it
holds and takes the cells it needs.

```
content out of flow at max-content, bounded by (maxW cells)
        ↓ one synchronous getBoundingClientRect
w = ceil((width  + border + gutter) / pitch)
h = ceil((height + header + border + gutter) / pitch)
        ↓ clamp to min/max/aspect
```

Three things in that are not obvious and are all load-bearing:

- **Out of flow at `max-content`.** Measuring the content where it sits gives
  back the size of the track being computed — the circularity is the whole
  difficulty of auto-sizing inside a grid.
- **The styles are written straight to the node**, not through a reactive class.
  Svelte flushes the DOM on a microtask, so a `measuring = true` on the line
  above is still pending when the rect is read. They are restored before
  anything paints.
- **Bounded by the card's own max width.** A long line has to wrap somewhere,
  and the widest the card may ever be is the only honest place. This is also why
  a card can *shrink*: the usual grow-only heuristic (`scrollHeight >
  clientHeight → grow`) leaves a card the size of the largest thing it ever
  held.

A `ResizeObserver` re-runs it when the content changes, so a result that arrives
or a log that grows re-sizes its card without anyone asking. **Resizing a card by
hand clears `auto`** — a card the user has sized is no longer the content's to
size.

**What is measured is written down.** The board reports the size it arrived at
and the document keeps it, so `auto` describes where a size *came from* rather
than whether one exists. Without that the document holds a card with no
footprint, and §2 explains what that cost: an invariant cannot be kept about
something the keeper cannot see. A file therefore gains `w`/`h` for cards that
had none, on the first open that measures them — which is a real edit, and worth
it for a board whose rules hold.

---

## 4. Interaction

| Gesture | Effect |
|---|---|
| Drag the header | Move. The card snaps cell to cell. |
| Drag any edge or corner | Resize, clamped by the card's constraints. |
| Double-click the header | Maximize into the free room around it; again to restore. |
| Hover free cells | A faint plus on the cell; click it for a card there. |
| Drag across free cells | Draw a card's rectangle; release to make it. |
| Right-click a free cell | New card there, of the kinds that fit. |
| Right-click a card | That card's menu: maximize, focus, send to a page, remove. |
| Focus the header, arrows | Move one cell. |
| Focus the header, shift+arrows | Resize one cell. |
| Long press a card | Focus it alone; press and hold again to leave. |
| Click a card | Select it. Shift-click adds; empty space clears. |
| ⌘/Ctrl+F | Focus the selected card, or leave focus. |
| ⌘/Ctrl+Backspace | Delete the selection. |
| ⌘/Ctrl+Enter | Maximize the selection. |
| ⌘/Ctrl+D | Duplicate the selection. |
| ⌘/Ctrl+A | Select every card on the page. |
| `?` | The whole list, in a sheet. |
| Double-click a card's name | Rename it in place. Enter keeps, Escape abandons. |
| ⌘/Ctrl `+` `-` `0`, ctrl+wheel | Zoom the lattice: bigger cells, same coordinates. |

**Every action has a key, and every key is a chord.** Bare letters belong to the
cards — `f` will mean the letter f in a program long before it means focus — so
everything but the arrows takes the platform modifier. The arrows are safe
because nothing else wants them while a card, and not a text field, has the
keyboard. The list lives in `components/Bento/shortcuts.js`, next to the code
that acts on it and rendered by the help sheet: a list of shortcuts maintained
somewhere else is a list that is wrong by the second release.

**A selection is a set.** Everything you can do to one card you can do to the
several you picked out: they move together, resize together, duplicate together
and delete together, and the group is clamped by whichever member reaches an edge
first, so the shape a selection makes never changes on the way. Dragging a card
that is in the selection carries the selection; dragging one that is not is about
that card alone. A click that never became a drag narrows the selection to the
card you clicked, because otherwise a selection could only ever grow.

**Resizing pulls edges, not a handle.** All eight: four sides, four corners,
invisible, ~8px wide with the corners winning where they meet. Pulling a top or
left edge moves the card as it shrinks — that is what pulling *that* edge means,
and the opposite edge is the one that must not move. When a constraint refuses
the size, the moving edge stays put instead of sliding the card sideways for
free.

**Nothing is drawn that is not content.** No border on a card, no rule under its
header, no grip in its corner: a card is a surface a little above the board, and
outlining things that space already separates is drawing boxes for their own
sake. The cursor is the resize affordance, as it is for every window on the
desktop. The lattice itself fades in only while you are arranging — "where will
this land" is not a question anyone is asking until they have a card in hand —
and the size read-out appears on hover. The one line the board will draw is the
outline on a refused drop.

**Maximize takes only free room.** It grows a cell at a time in each direction
while the space is empty; it never displaces anyone, because a double-click that
shoved three cards aside would be a surprise. The previous rectangle is
remembered, so the same gesture restores it.

**Focus is a way of looking, not a change.** One card fills the board and the
rest are hidden; the card's real cells are untouched, and `Escape` gives them
back. It is `view` state on the server, not a browser-local mode, so an agent
asking what the user is looking at gets the answer the user would give.

**The card snaps; it does not glide.** It moves a whole cell when the pointer
passes the halfway mark, rounded rather than floored, so the card is always
standing exactly where releasing it would leave it. A card that follows the
pointer in pixels and jumps on release spends the whole gesture showing you a
position that is not a position.

Cards are placed by `transform`, not by grid lines. Two reasons, both visible: a
transform does not re-lay-out the board on every pointer move — doing that made
dragging flicker — and a transform is the only thing here that can be
transitioned, which is what lets a displaced card *slide*. The card under the
pointer has transitions off; easing a snap only blurs where it landed.

### Making room

Cards that the dragged card is on top of **step aside**, and step back the moment
it moves away. The arrangement is a pure function of (layout, dragged rectangle),
recomputed each frame from the untouched layout — so there is no "put it back"
path to get wrong and no drift after a long drag. A displaced card is shoved in
the direction of travel first and to its nearest free cell second; the shove is
what makes it read as sliding tiles rather than as cards teleporting to wherever
there happened to be room. If somebody cannot be housed at all, the whole
arrangement is refused and the dragged card is outlined in the danger colour.

**A drop keeps the arrangement.** Everyone who stepped aside stays where they
stepped: cards that avoid a drag and then snap back the instant it ends are
cards that overlap. The whole thing lands as one `board.arrange` action, not a
burst of moves — sent separately, the document really does hold an overlapping
layout between the first move and the last, and a save, a reload or an agent
reading the workspace at that moment sees it broken.

Out-of-bounds is still refused outright, and a card is never pushed onto another
page.

---

## 5. Pages

A page is a bounded rectangle of cells that **fills the window**. The document's
`cols`/`rows` are a *minimum*: the board adds whatever further whole cells fit on
this screen, so the lattice is the viewport (less the pager's line), a card can
be dragged and grown to the edge of it, and a layout authored on a small screen
still opens intact on a large one — every cell it used is still there, with more
beside it. Whole cells only: half a cell is somewhere a card could be dropped and
not fit.

The board extends past what a viewport can hold by paging, not by growing without
limit: a bounded page lets a position keep its meaning ("top-left of page 2")
instead of being a coordinate somewhere in an unbounded plane.

**Pages are not a list.** `pageCount` is derived: one more than the highest page
any card is on, or the page you are standing on, whichever is greater. That is
the whole of "add a page" and "remove a page" — walk past the last page with the
pager's `+` and you are on a new one; send the last card off a page and the page
is gone when you leave it. There is no collection of pages to keep in step with
the cards, so a page cannot go missing and an empty one cannot be left behind.
Cards move between pages from their own menu.

---

## 6. What the board does not do

- **It works where nobody is looking.** A tab that is not rendering — hidden,
  backgrounded, driven by a test — measures zero, and a board that believed that
  would collapse to one cell and refuse every move as out of bounds. The
  document's own `cols`/`rows` are the answer when the window will not give one,
  so an agent driving a workspace whose only client is a background tab gets the
  same board a person would.
- **It does not scroll, ever.** Zoom is a wish, not a command: cells grow until
  a card would fall off the page and no further, and shrinking the window
  shrinks the cells rather than hiding anything. A bounded page you can see all
  of is what makes a position mean something; half a board behind an edge is
  worse than a small board. The floor is a quarter of the base pitch, which no
  realistic layout reaches.
- It does not know what is in a card. Content is a snippet the caller renders;
  the board supplies geometry and gestures.
- It does not persist anything, and it does not apply its own gestures.
  `onarrange`, `onadd` and `onremove` hand out what the user did; the document
  store decides what happens ([ui-workspace.md](ui-workspace.md)) and the new
  layout arrives back through `cards`. A board that also kept state would be a
  second copy of the layout, and two copies is one bug waiting for the day they
  disagree.

---

## 7. Open

- **Dragging a card to a page.** The card menu sends it; dragging past the edge
  of the board would be the better gesture and is not implemented.
- **Tidy/pack.** Still absent, and still an action if it lands: making room for a
  drag is one thing, rearranging a board nobody asked to rearrange is another.
- **A card creating itself where there is no room.** The menu says "no room"
  rather than finding a spot elsewhere; offering to place a card somewhere the
  user is not pointing is a guess.
- **Overflow inside a card** is a plain scroll container today. Whether a card
  that cannot fit its content should say so is a content question, not a board
  question.
