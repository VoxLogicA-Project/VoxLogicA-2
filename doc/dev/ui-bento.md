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

---

## 4. Interaction

| Gesture | Effect |
|---|---|
| Drag the header | Move. The card follows the lattice live. |
| Drag the corner grip | Resize, clamped by the card's constraints. |
| Focus the header, arrows | Move one cell. |
| Focus the header, shift+arrows | Resize one cell. |

Rounded, not floored: the card changes cell when the pointer passes the halfway
mark, which is where the eye already thinks it moved. Nothing is committed until
the pointer is released, and the header is focusable because a board only a
pointer can arrange is a board some people cannot arrange.

**Collisions are refused, not resolved.** A drop onto occupied or out-of-bounds
cells snaps back, and the card is outlined in the danger colour while the pointer
holds it there. Cards that shove each other around produce a layout nobody can
predict, and predictability is the reason for having a lattice at all.
Auto-packing, if it is ever wanted, belongs behind an explicit action ("tidy"),
where the user asked for it.

---

## 5. Pages

A page is a fixed rectangle of `cols × rows` cells. The board extends past what
a viewport can hold by paging, not by growing without limit: a bounded page lets
a position keep its meaning ("top-left of page 2") instead of being a coordinate
somewhere in an unbounded plane. The pager appears only when a second page has
cards on it.

---

## 6. What the board does not do

- It does not scroll. If a page's worth of cells does not fit, the answer is
  zoom or a smaller page, not a scrollbar that hides half the lattice.
- It does not know what is in a card. Content is a snippet the caller renders;
  the board supplies geometry and gestures.
- It does not persist anything. `onchange` hands the layout out; the document
  store owns it ([ui-workspace.md](ui-workspace.md)).

---

## 7. Open

- **Moving a card between pages.** Dragging to the edge is the obvious gesture
  and is not implemented.
- **Creating and deleting cards.** The board has no affordance for either yet;
  they are actions on the document, not on the board.
- **Tidy/pack.** Deliberately absent (§4). If it lands it is an action.
- **Zoom UI.** The `zoom` prop exists and nothing sets it.
- **Overflow inside a card** is a plain scroll container today. Whether a card
  that cannot fit its content should say so is a content question, not a board
  question.
