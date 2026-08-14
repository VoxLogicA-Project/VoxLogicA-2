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
   * A page is a fixed rectangle of `cols x rows` cells. The board extends past
   * what the viewport can hold by paging, not by growing without limit: a
   * bounded page is what lets a card keep its meaning ("top-left of page 2")
   * instead of drifting somewhere in an unbounded plane.
   *
   * Placement is free and collisions are refused, not resolved: dropping a card
   * on occupied cells snaps it back. Cards that shove each other around are a
   * layout you cannot predict, and predictability is the point of a lattice.
   * Auto-packing, if it is ever wanted, belongs behind an explicit action.
   *
   * See doc/dev/ui-bento.md for the model, the units, and what is still open.
   */
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
     * layout does, and the new layout comes back through `cards`. */
    onmove = undefined,
    onresize = undefined,
    onpage = undefined,
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
  /** Resolved pixel pitch and gutter, measured rather than parsed from CSS. */
  let pitch = $state(0);
  let gutter = $state(0);

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
    if (!board) return;
    const measure = () => {
      const style = getComputedStyle(board);
      gutter = parseFloat(style.columnGap) || 0;
      // The first resolved track, not the element's width: a grid container is
      // a block and may be wider than its own tracks, and dividing that width
      // by `cols` would then hand the drag maths a pitch the grid never used.
      pitch = (parseFloat(style.gridTemplateColumns) || 0) + gutter;
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(board);
    return () => observer.disconnect();
  });

  /** Cells [x, x+w) x [y, y+h) are inside the page and free of other cards. */
  function canPlace(id, x, y, w, h) {
    if (x < 0 || y < 0 || x + w > cols || y + h > rows) return false;
    return !visible.some((other) => {
      if (other.id === id) return false;
      const size = sizeOf(other);
      return (
        x < other.x + size.w && other.x < x + w && y < other.y + size.h && other.y < y + h
      );
    });
  }
</script>

<div class="bento">
  <div
    bind:this={board}
    class="board"
    role="group"
    aria-label={label}
    style="--cols: {cols}; --rows: {rows}; --zoom: {zoom};"
  >
    {#each visible as card (card.id)}
      <BentoCard
        {card}
        {pitch}
        {gutter}
        {cols}
        {rows}
        {canPlace}
        size={sizeOf(card)}
        onmove={(x, y) => onmove?.(card.id, x, y)}
        onresize={(w, h) => onresize?.(card.id, w, h)}
        onmeasure={(w, h) => (measured = { ...measured, [card.id]: { w, h } })}
      >
        {@render children?.(card)}
      </BentoCard>
    {/each}
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
