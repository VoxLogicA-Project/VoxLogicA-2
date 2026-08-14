<script>
  /**
   * The board: a lattice of cells that cards are placed, moved and resized on.
   *
   * Positions are integer cell coordinates, never pixels. That is the whole
   * design: a layout you saved is the layout you get back on any screen, two
   * cards the same width *are* the same width, and "is this spot free" is a
   * decidable question rather than a hit test with a tolerance. The pixel size
   * of a cell is one CSS variable (`--bento-pitch`, in rem) times `zoom`, so
   * scaling the board never touches the model.
   *
   * A page is a bounded rectangle of cells, and it fills the space it is given:
   * `cols`/`rows` from the document are a *minimum*, and the board adds whatever
   * further whole cells fit on this screen. So the lattice is the viewport, a
   * card can be dragged and grown to the edge of it, and a layout authored on a
   * small screen still opens intact on a large one -- the cells it used are all
   * still there, with more beside them. Paging, not unbounded scrolling, is what
   * lets a card keep a meaning like "top-left of page 2".
   *
   * Placement is free and collisions are refused, not resolved: dropping a card
   * on occupied cells snaps it back. Cards that shove each other around are a
   * layout you cannot predict, and predictability is the point of a lattice.
   * Auto-packing, if it is ever wanted, belongs behind an explicit action.
   *
   * See doc/dev/ui-bento.md for the model, the units, and what is still open.
   */
  import ContextMenu from "../ContextMenu/ContextMenu.svelte";
  import BentoCard from "./BentoCard.svelte";

  let {
    /** `[{ id, x, y, w?, h?, page?, auto?, minW?, minH?, maxW?, maxH?, aspect?, title? }]` */
    cards = [],
    cols = 12,
    rows = 8,
    page = 0,
    zoom = 1,
    label = "Board",
    /** A gesture finished. The board does not apply it -- the owner of the
     * layout does, and the new layout comes back through `cards`.
     *
     * `onarrange` carries the whole result of one drag: the card that moved and
     * everyone it displaced, as `[{ id, x?, y?, w?, h? }]`. One gesture is one
     * change; sent as separate moves, the layout is briefly overlapping and
     * anything watching can see it that way. */
    onarrange = undefined,
    onpage = undefined,
    /** `(kind, x, y, w, h)` — a new card was asked for on empty cells. */
    onadd = undefined,
    /** `(id)` — a card was asked to go away. */
    onremove = undefined,
    /** What `onadd` may be asked for, and how big each starts. */
    kinds = [
      { kind: "code", label: "Code card", w: 5, h: 3 },
      { kind: "result", label: "Result card", w: 4, h: 3 },
      { kind: "note", label: "Note", w: 4, h: 2 },
    ],
    /** Snippet rendering a card's content; receives the card. */
    children,
  } = $props();

  /** The board renders a layout; it does not own one.
   *
   * Everything it knows arrives as props and every gesture leaves as a callback,
   * so the one copy of the truth is wherever the caller keeps it -- in the app,
   * the workspace replica, which the server owns. A board that also kept state
   * would be a second copy, and two copies of a layout is one bug waiting for
   * the day they disagree.
   */
  const items = $derived(cards.map((card) => ({ page: 0, auto: false, ...card })));

  /** Measured sizes for auto cards, which have no w/h of their own. Kept here
   * because occupancy is a question about the whole board. */
  let measured = $state({});

  let board = $state(null);
  let canvas = $state(null);
  /** Resolved pixel pitch and gutter, measured rather than parsed from CSS. */
  let pitch = $state(0);
  let gutter = $state(0);
  /** Whole cells the canvas has room for; the props are the floor, not the cap. */
  let fit = $state({ cols: 0, rows: 0 });

  const width = $derived(Math.max(cols, fit.cols));
  const height = $derived(Math.max(rows, fit.rows));

  const pageCount = $derived(Math.max(1, ...items.map((card) => card.page + 1)));
  const visible = $derived(items.filter((card) => card.page === page));

  /** A card's effective size: what it was given, or what it measured. */
  function sizeOf(card) {
    return { w: card.w ?? measured[card.id]?.w ?? 1, h: card.h ?? measured[card.id]?.h ?? 1 };
  }

  // The grid owns the geometry; JS asks it what it did rather than recomputing
  // it from the tokens. Two sources for one number is two chances to disagree,
  // and the drag maths has to land on the same cells the grid drew.
  $effect(() => {
    if (!board || !canvas) return;
    const measure = () => {
      const style = getComputedStyle(board);
      gutter = parseFloat(style.columnGap) || 0;
      // The first resolved track, not the element's width: a grid container is
      // a block and may be wider than its own tracks, and dividing that width
      // by `cols` would then hand the drag maths a pitch the grid never used.
      const step = (parseFloat(style.gridTemplateColumns) || 0) + gutter;
      pitch = step;
      if (!step) return;
      // How many whole cells the space around the board could hold. Whole ones
      // only: half a cell is somewhere a card can be dropped and not fit.
      const box = canvas.getBoundingClientRect();
      fit = {
        cols: Math.floor((box.width + gutter) / step),
        rows: Math.floor((box.height + gutter) / step),
      };
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(board);
    observer.observe(canvas);
    return () => observer.disconnect();
  });

  /** Cells [x, x+w) x [y, y+h) are inside the page and free of other cards. */
  function canPlace(id, x, y, w, h) {
    if (x < 0 || y < 0 || x + w > width || y + h > height) return false;
    return !visible.some((other) => {
      if (other.id === id) return false;
      const size = sizeOf(other);
      return (
        x < other.x + size.w && other.x < x + w && y < other.y + size.h && other.y < y + h
      );
    });
  }

  // ------------------------------------------------------- making room, live

  const overlaps = (a, b) =>
    a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  /** Where every card sits while `rect` is being dragged over the board.
   *
   * A pure function of the layout and the dragged rectangle, which is the whole
   * trick: cards do not "get pushed" and then need putting back. Each frame the
   * arrangement is recomputed from the untouched layout, so a card is displaced
   * exactly while the dragged card is on top of it and is home again the moment
   * it is not. There is no undo to get wrong, and no drift after a long drag.
   *
   * Displacement is a shove in the direction of travel first, and the nearest
   * free spot second -- the shove is what makes it read as sliding tiles rather
   * than as cards teleporting to wherever there was room. Returns `null` if
   * somebody cannot be housed, which is what makes the drop refuse.
   */
  function arrange(id, rect, push) {
    const others = visible.filter((card) => card.id !== id);
    const fixed = [];
    const homeless = [];
    for (const card of others) {
      const box = { ...sizeOf(card), x: card.x, y: card.y };
      (overlaps(box, rect) ? homeless : fixed).push({ card, box });
    }
    if (homeless.length === 0) return new Map();

    const taken = [rect, ...fixed.map((entry) => entry.box)];
    const moved = new Map();
    // Nearest first: the card the dragged one has barely touched moves before
    // the card it is sitting on, which keeps the shuffle small and legible.
    homeless.sort((a, b) => Math.hypot(a.box.x - rect.x, a.box.y - rect.y) - Math.hypot(b.box.x - rect.x, b.box.y - rect.y));

    for (const { card, box } of homeless) {
      const spot = findSpot(box, taken, push);
      if (!spot) return null;
      taken.push({ ...box, ...spot });
      moved.set(card.id, spot);
    }
    return moved;
  }

  /** The closest free position for `box`, preferring the push direction. */
  function findSpot(box, taken, push) {
    const free = (x, y) =>
      x >= 0 &&
      y >= 0 &&
      x + box.w <= width &&
      y + box.h <= height &&
      !taken.some((other) => overlaps({ x, y, w: box.w, h: box.h }, other));

    // 1. Straight along the shove, as far as it takes to clear.
    if (push.dx || push.dy) {
      for (let step = 1; step <= Math.max(width, height); step += 1) {
        const x = box.x + push.dx * step;
        const y = box.y + push.dy * step;
        if (free(x, y)) return { x, y };
      }
    }
    // 2. Otherwise the nearest cell that fits, by rings around home.
    for (let radius = 1; radius <= width + height; radius += 1) {
      for (let dy = -radius; dy <= radius; dy += 1) {
        for (let dx = -radius; dx <= radius; dx += 1) {
          if (Math.abs(dx) + Math.abs(dy) !== radius) continue;
          if (free(box.x + dx, box.y + dy)) return { x: box.x + dx, y: box.y + dy };
        }
      }
    }
    return null;
  }

  /** The gesture in flight: which card, where it is, and which way it came. */
  let drag = $state(null);
  const displaced = $derived(drag ? arrange(drag.id, drag.rect, drag.push) : null);
  const refused = $derived(drag !== null && displaced === null);

  function preview(card, rect) {
    if (rect === null) {
      drag = null;
      return;
    }
    const from = drag?.rect ?? { x: card.x, y: card.y };
    // The push is the last actual movement, not the total: a card dragged left
    // and then back right shoves right, which is what the hand just did.
    const push = drag
      ? {
          dx: Math.sign(rect.x - from.x) || drag.push.dx,
          dy: Math.sign(rect.y - from.y) || drag.push.dy,
        }
      : { dx: 0, dy: 0 };
    drag = { id: card.id, rect, push };
  }

  // ------------------------------------------- holding a drop until it lands

  /** Positions already committed but not yet echoed back through `cards`.
   *
   * Without this the card snaps home for the length of one server round trip
   * and then jumps to where it was dropped -- the flash. The board keeps
   * drawing what it was told until the layout it is given agrees, or until the
   * grace period says the change was refused after all.
   */
  let pending = $state({});
  let pendingSince = 0;

  $effect(() => {
    if (Object.keys(pending).length === 0) return;
    // Read the layout so this re-runs whenever it changes.
    const settled = Object.fromEntries(
      Object.entries(pending).filter(([id, spot]) => {
        const card = items.find((entry) => entry.id === id);
        if (!card) return false;
        const size = sizeOf(card);
        const same =
          card.x === spot.x &&
          card.y === spot.y &&
          (spot.w === undefined || size.w === spot.w) &&
          (spot.h === undefined || size.h === spot.h);
        return !same;
      }),
    );
    if (Object.keys(settled).length !== Object.keys(pending).length) pending = settled;
    // A refusal never arrives as a change, so it has to time out. Long enough
    // for a round trip, short enough that a rejected drop does not linger.
    const timer = setTimeout(() => {
      if (Date.now() - pendingSince >= 900) pending = {};
    }, 900);
    return () => clearTimeout(timer);
  });

  function positionOf(card) {
    return pending[card.id] ?? { x: card.x, y: card.y };
  }

  /** Where a card is drawn right now: mid-drag arrangement first, then a
   * committed-but-unconfirmed spot, then the layout itself. */
  function placeOf(card) {
    if (drag?.id === card.id) return drag.rect;
    return displaced?.get(card.id) ?? positionOf(card);
  }

  function commit(card, rect) {
    // The whole arrangement is committed, not just the card under the finger:
    // everything that stepped aside to make room stays where it stepped. A card
    // that avoided the drag and then snapped back the moment the drag ended
    // would leave the two of them overlapping, which is the one state this
    // board is built never to be in.
    const spots = { [card.id]: rect };
    if (displaced) for (const [id, spot] of displaced) spots[id] = spot;
    pending = { ...pending, ...spots };
    pendingSince = Date.now();
    drag = null;

    const changed = Object.entries(spots)
      .map(([id, spot]) => {
        const before = items.find((entry) => entry.id === id);
        if (!before) return null;
        const size = sizeOf(before);
        const placement = { id };
        if (before.x !== spot.x) placement.x = spot.x;
        if (before.y !== spot.y) placement.y = spot.y;
        if (spot.w !== undefined && spot.w !== size.w) placement.w = spot.w;
        if (spot.h !== undefined && spot.h !== size.h) placement.h = spot.h;
        return Object.keys(placement).length > 1 ? placement : null;
      })
      .filter(Boolean);
    if (changed.length) onarrange?.(changed);
  }

  // ------------------------------------------------------------ adding cards

  /** The cell the last right-click landed on; where a new card goes. */
  let target = $state({ x: 0, y: 0 });

  function onBoardContextMenu(event) {
    if (!pitch) return;
    const box = board.getBoundingClientRect();
    target = {
      x: Math.min(Math.max(Math.floor((event.clientX - box.left) / pitch), 0), width - 1),
      y: Math.min(Math.max(Math.floor((event.clientY - box.top) / pitch), 0), height - 1),
    };
  }

  /** The largest free rectangle at `target`, up to the kind's own size.
   *
   * A new card takes what is actually there rather than refusing to appear
   * because its default size would have overlapped something: clicking an empty
   * corner should produce a card, not a shrug. `null` when even one cell is
   * taken, which is the only honest refusal.
   */
  function room(kind) {
    if (!canPlace(null, target.x, target.y, 1, 1)) return null;
    let w = 1;
    let h = 1;
    while (w < kind.w && canPlace(null, target.x, target.y, w + 1, h)) w += 1;
    while (h < kind.h && canPlace(null, target.x, target.y, w, h + 1)) h += 1;
    return { w, h };
  }

  const addItems = $derived(
    kinds.map((kind) => {
      const size = room(kind);
      return {
        label: kind.label,
        hint: size ? `${size.w}×${size.h}` : "no room",
        disabled: size === null,
        onselect: () => onadd?.(kind.kind, target.x, target.y, size.w, size.h),
      };
    }),
  );
</script>

<div class="bento">
  <!-- The empty lattice is where a card comes from: right-click a free cell and
       the menu offers the kinds that fit there. A toolbar button would have to
       guess where the card goes; the cell you pointed at does not. -->
  <div bind:this={canvas} class="canvas" oncontextmenucapture={onBoardContextMenu}>
    <ContextMenu label="Board" items={addItems}>
      <div
        bind:this={board}
        class="board"
        role="group"
        aria-label={label}
        style="--cols: {width}; --rows: {height}; --zoom: {zoom};"
      >
        {#each visible as card (card.id)}
          <BentoCard
            {card}
            {pitch}
            {gutter}
            cols={width}
            rows={height}
            at={placeOf(card)}
            invalid={refused && drag?.id === card.id}
            size={sizeOf(card)}
            onremove={onremove ? () => onremove(card.id) : undefined}
            onpreview={(rect) => preview(card, rect)}
            oncommit={(rect) => commit(card, rect)}
            onmeasure={(w, h) => (measured = { ...measured, [card.id]: { w, h } })}
          >
            {@render children?.(card)}
          </BentoCard>
        {/each}
      </div>
    </ContextMenu>
  </div>

  {#if pageCount > 1}
    <nav class="pager" aria-label="Board pages">
      <button type="button" disabled={page === 0} onclick={() => onpage?.(page - 1)}>‹</button>
      <span class="numeric">page {page + 1} / {pageCount}</span>
      <button
        type="button"
        disabled={page >= pageCount - 1}
        onclick={() => onpage?.(page + 1)}
      >
        ›
      </button>
    </nav>
  {/if}
</div>

<style>
  .bento {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
    /* The board takes the room it is given; the pager keeps its own line. */
    flex: 1;
    min-height: 0;
  }

  .canvas {
    flex: 1;
    min-height: 0;
    min-width: 0;
  }

  .board {
    /* One cell is `pitch - gutter`; a card `w` cells wide spans `w` tracks plus
     * the `w - 1` gutters between them, which is exactly `w * pitch - gutter`. */
    --pitch: calc(var(--bento-pitch) * var(--zoom));
    --cell: calc(var(--pitch) - var(--bento-gutter));
    display: grid;
    grid-template-columns: repeat(var(--cols), var(--cell));
    grid-template-rows: repeat(var(--rows), var(--cell));
    gap: var(--bento-gutter);
    /* Exactly as wide as the lattice, so the dotted grid below marks cells that
     * exist rather than implying a board that continues past its own edge. */
    width: max-content;
    /* The tracks draw the lattice and give the board its size; the cards sit on
     * top of it, positioned by transform. A grid item cannot be animated
     * between cells -- grid lines do not interpolate -- and re-laying out the
     * whole grid on every pointer move is what made dragging flicker. */
    position: relative;
    /* The lattice is drawn, faintly: an empty board that shows its cells tells
     * you what dragging will snap to before you drag anything. */
    background-image: radial-gradient(
      circle at 1px 1px,
      var(--color-border) 1px,
      transparent 0
    );
    background-size: var(--pitch) var(--pitch);
    background-position: calc(var(--bento-gutter) / -2) calc(var(--bento-gutter) / -2);
  }

  .pager {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    align-self: center;
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  .pager button {
    padding: 0 var(--space-2);
    border: var(--border-width) solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    color: var(--color-text-muted);
  }

  .pager button:hover:not(:disabled) {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .pager button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
</style>
