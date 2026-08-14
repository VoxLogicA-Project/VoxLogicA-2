<script>
  /**
   * One card on the board: placed by cell, moved by its header, resized by its
   * corner, and sized to its own content when nothing says otherwise.
   *
   * Internal to `Bento` -- it needs the board's pitch, bounds and occupancy test
   * to do anything correct, so it is not exported from the library on its own.
   *
   * Auto-sizing measures the content at `max-content` for a single frame and
   * rounds up to whole cells, which is why it can shrink as well as grow: a
   * card that only ever grew would keep the size of the largest thing it ever
   * held. The measurement is bounded by the card's own max constraints, so a
   * long line wraps instead of asking for a card wider than the page.
   *
   * Dragging is live and reverts: the card follows the pointer on the lattice,
   * and a drop onto occupied or out-of-bounds cells snaps back to where it came
   * from. Nothing is committed to the model until the pointer is released.
   */
  let {
    card,
    /** The card's effective size in cells: given, or last measured. */
    size,
    pitch,
    gutter,
    cols,
    rows,
    canPlace,
    onmove,
    onresize,
    onmeasure,
    children,
  } = $props();

  let root = $state(null);
  let headerEl = $state(null);
  let content = $state(null);
  /** Live position while dragging; `null` when the model is the truth. */
  let preview = $state(null);
  let gesture = null;

  const at = $derived(preview ?? { x: card.x, y: card.y, w: size.w, h: size.h });
  const invalid = $derived(
    preview !== null && !canPlace(card.id, preview.x, preview.y, preview.w, preview.h),
  );

  function clamp(w, h) {
    let cw = Math.min(Math.max(w, card.minW ?? 1), card.maxW ?? cols, cols);
    let ch = Math.min(Math.max(h, card.minH ?? 1), card.maxH ?? rows, rows);
    if (card.aspect) {
      // Width leads: it is the axis the eye compares across a row of cards.
      ch = Math.min(Math.max(Math.round(cw / card.aspect), card.minH ?? 1), rows);
    }
    return [cw, ch];
  }

  // ------------------------------------------------------------- auto-sizing

  function autoSize() {
    if (!card.auto || !content || !pitch) return;
    // One synchronous layout read against `max-content`, out of flow so the
    // grid track cannot constrain the answer we are trying to obtain from it.
    // Written straight to the node, not through a reactive class: Svelte flushes
    // the DOM on a microtask, so a `measuring = true` on the line above would
    // still be pending when the rect is read, and what came back would be the
    // size of the track we are trying to compute. This is a measurement, not
    // rendering; it is put back before anything can paint.
    const style = content.getAttribute("style");
    Object.assign(content.style, {
      position: "absolute",
      visibility: "hidden",
      width: "max-content",
      height: "max-content",
      // The only bound while measuring is the card's own maximum width: a long
      // line has to wrap somewhere, and the widest the card may ever be is the
      // only honest place for it to do so.
      maxWidth: `${(card.maxW ?? cols) * pitch - gutter}px`,
    });
    const box = content.getBoundingClientRect();
    if (style === null) content.removeAttribute("style");
    else content.setAttribute("style", style);
    // The card is not only its content: the header and the border take cells
    // too, and a card sized to the content alone clips by exactly that much.
    const border = 2 * parseFloat(getComputedStyle(root).borderTopWidth || 0);
    const chrome = (headerEl?.offsetHeight ?? 0) + border;
    const [w, h] = clamp(
      Math.ceil((box.width + border + gutter) / pitch),
      Math.ceil((box.height + chrome + gutter) / pitch),
    );
    if (w !== size.w || h !== size.h) onmeasure?.(w, h);
  }

  $effect(() => {
    if (!card.auto || !content || !pitch) return;
    autoSize();
    // Content that changes size later -- a result that arrives, a log that
    // grows -- re-sizes the card without anyone asking.
    const observer = new ResizeObserver(() => autoSize());
    observer.observe(content);
    return () => observer.disconnect();
  });

  // ---------------------------------------------------------------- gestures

  function begin(event, mode) {
    if (event.button !== 0 || !pitch) return;
    event.preventDefault();
    try {
      // Capture keeps the gesture on this element when the pointer outruns the
      // card, which it always does on a fast drag. Not fatal if it is refused.
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* no active pointer with this id; the gesture still works, just leakier */
    }
    gesture = {
      mode,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      from: { x: card.x, y: card.y, w: size.w, h: size.h },
    };
    preview = { ...gesture.from };
  }

  function move(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    // Rounded, not floored: the card changes cell when the pointer passes the
    // half-way mark, which is where the eye already thinks it moved.
    const dx = Math.round((event.clientX - gesture.x) / pitch);
    const dy = Math.round((event.clientY - gesture.y) / pitch);
    const from = gesture.from;
    if (gesture.mode === "move") {
      preview = {
        ...from,
        x: Math.min(Math.max(from.x + dx, 0), cols - from.w),
        y: Math.min(Math.max(from.y + dy, 0), rows - from.h),
      };
    } else {
      const [w, h] = clamp(from.w + dx, from.h + dy);
      preview = { ...from, w: Math.min(w, cols - from.x), h: Math.min(h, rows - from.y) };
    }
  }

  function end(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const next = preview;
    gesture = null;
    preview = null;
    if (!next) return;
    if (!canPlace(card.id, next.x, next.y, next.w, next.h)) return; // snaps back
    // Reported, not applied: the card draws a layout, it does not own one. The
    // change comes back as a prop once whoever owns it has accepted it.
    if (next.x !== card.x || next.y !== card.y) onmove?.(next.x, next.y);
    // Resizing by hand is also what ends auto-sizing, and the owner records
    // that by the card having a w/h at all.
    if (next.w !== size.w || next.h !== size.h) onresize?.(next.w, next.h);
  }

  /** The same two gestures from the keyboard, which is the only way some people
   * have of arranging anything at all. */
  function onKeydown(event) {
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[
      event.key
    ];
    if (!step) return;
    event.preventDefault();
    const [dx, dy] = step;
    if (event.shiftKey) {
      const [w, h] = clamp(size.w + dx, size.h + dy);
      if (canPlace(card.id, card.x, card.y, w, h)) onresize?.(w, h);
      return;
    }
    if (canPlace(card.id, card.x + dx, card.y + dy, size.w, size.h)) {
      onmove?.(card.x + dx, card.y + dy);
    }
  }
</script>

<article
  bind:this={root}
  data-card-id={card.id}
  class="card"
  class:dragging={preview !== null}
  class:invalid
  style="grid-column: {at.x + 1} / span {at.w}; grid-row: {at.y + 1} / span {at.h};"
  aria-label={card.title ?? card.id}
>
  <!-- The header is the drag handle, and it is focusable: a card you can only
       move with a pointer is a card some people cannot move. -->
  <header
    bind:this={headerEl}
    role="button"
    tabindex="0"
    aria-label="{card.title ?? card.id} — move with arrows, resize with shift+arrows"
    onpointerdown={(event) => begin(event, "move")}
    onpointermove={move}
    onpointerup={end}
    onpointercancel={end}
    onkeydown={onKeydown}
  >
    <span class="title">{card.title ?? card.id}</span>
    <span class="size numeric">{at.w}×{at.h}</span>
  </header>

  <div class="body">
    <div bind:this={content} class="content">
      {@render children?.()}
    </div>
  </div>

  <button
    type="button"
    class="grip"
    aria-label="Resize {card.title ?? card.id}"
    onpointerdown={(event) => begin(event, "resize")}
    onpointermove={move}
    onpointerup={end}
    onpointercancel={end}
    onkeydown={onKeydown}
  ></button>
</article>

<style>
  .card {
    display: flex;
    flex-direction: column;
    position: relative;
    min-width: 0;
    min-height: 0;
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
  }

  .card.dragging {
    /* Lifted while it is in the air, and only then: a shadow that is always on
     * says everything is floating, which says nothing. */
    box-shadow: var(--shadow-overlay);
    z-index: var(--layer-overlay);
  }

  .card.invalid {
    border-color: var(--color-danger);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-1) var(--space-3);
    border-bottom: var(--border-width) solid var(--color-border);
    cursor: grab;
    touch-action: none; /* the pointer belongs to the drag, not to scrolling */
    user-select: none;
  }

  .card.dragging header {
    cursor: grabbing;
  }

  .title {
    font-size: var(--text-xs);
    font-weight: var(--weight-semibold);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .size {
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .body {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }

  .content {
    padding: var(--space-3);
  }

  .grip {
    position: absolute;
    right: 0;
    bottom: 0;
    width: var(--space-4);
    height: var(--space-4);
    cursor: nwse-resize;
    touch-action: none;
    /* Two short strokes rather than an icon: at 16px an icon is noise, and the
     * corner is discoverable by being the corner. */
    background:
      linear-gradient(
        135deg,
        transparent 0 45%,
        var(--color-border-strong) 45% 55%,
        transparent 55%
      );
  }

  .grip:hover {
    background:
      linear-gradient(
        135deg,
        transparent 0 45%,
        var(--color-text-muted) 45% 55%,
        transparent 55%
      );
  }
</style>
