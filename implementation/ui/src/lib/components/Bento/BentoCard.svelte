<script>
  /**
   * One card on the board: placed by cell, moved by its header, resized by its
   * corner, and sized to its own content when nothing says otherwise.
   *
   * Internal to `Bento` -- it needs the board's pitch and the arrangement the
   * board computes, so it is not exported from the library on its own.
   *
   * Positioned by transform, not by grid lines. Two reasons, both visible: a
   * transform does not relayout the board on every pointer move, which is what
   * made dragging flicker, and a transform is the only thing here that can be
   * transitioned, which is what makes a displaced card *slide* out of the way
   * instead of teleporting. The card under the pointer has transitions off: it
   * snaps cell to cell, and easing a snap only blurs where it landed.
   *
   * Auto-sizing measures the content at `max-content` for a single frame and
   * rounds up to whole cells, which is why it can shrink as well as grow: a
   * card that only ever grew would keep the size of the largest thing it ever
   * held. The measurement is bounded by the card's own max constraints, so a
   * long line wraps instead of asking for a card wider than the page.
   *
   * The card reports gestures and renders what it is told. It never applies a
   * move to itself: the board decides what the whole arrangement becomes, and
   * the owner of the layout decides whether that is what happens.
   */
  import ContextMenu from "../ContextMenu/ContextMenu.svelte";

  let {
    card,
    /** The card's effective size in cells: given, or last measured. */
    size,
    /** Where the board says this card sits right now, in cells. */
    at,
    pitch,
    gutter,
    cols,
    rows,
    /** True when the board could not arrange the drag this card is leading. */
    invalid = false,
    /** `(rect | null)` while a gesture runs; the board arranges around it. */
    onpreview,
    /** `(rect)` when the gesture is released and should be kept. */
    oncommit,
    onmeasure,
    onremove,
    children,
  } = $props();

  /** The card's own menu. Right-clicking a card is about *this* card, which is
   * why it stops there and never reaches the board's "new card here". */
  const menu = $derived(
    onremove ? [{ label: "Remove card", danger: true, onselect: onremove }] : [],
  );

  let root = $state(null);
  let headerEl = $state(null);
  let content = $state(null);
  /** True while a pointer owns this card. The card does not follow the pointer
   * in pixels: it snaps, cell by cell, as the pointer crosses each half-way
   * mark. A card that glides freely and then jumps on release shows you a
   * position that was never real; a card that snaps is always standing exactly
   * where dropping it would leave it. */
  let dragging = $state(false);
  let gesture = null;

  /** The drawn rectangle: whatever the board says, in pixels. */
  const px = $derived({
    x: at.x * pitch,
    y: at.y * pitch,
    w: (at.w ?? size.w) * pitch - gutter,
    h: (at.h ?? size.h) * pitch - gutter,
  });

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
    // Written straight to the node, not through a reactive class: Svelte flushes
    // the DOM on a microtask, so a `measuring = true` on the line above would
    // still be pending when the rect is read, and what came back would be the
    // size of the box we are trying to compute. This is a measurement, not
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
      from: { x: at.x, y: at.y, w: size.w, h: size.h },
      rect: { x: at.x, y: at.y, w: size.w, h: size.h },
    };
    dragging = true;
    onpreview?.(gesture.rect);
  }

  function move(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    const from = gesture.from;
    // Rounded, not floored: the card changes cell when the pointer passes the
    // half-way mark, which is where the eye already thinks it moved.
    if (gesture.mode === "move") {
      gesture.rect = {
        ...from,
        x: Math.min(Math.max(from.x + Math.round(dx / pitch), 0), cols - from.w),
        y: Math.min(Math.max(from.y + Math.round(dy / pitch), 0), rows - from.h),
      };
    } else {
      const [w, h] = clamp(from.w + Math.round(dx / pitch), from.h + Math.round(dy / pitch));
      gesture.rect = { ...from, w: Math.min(w, cols - from.x), h: Math.min(h, rows - from.y) };
    }
    onpreview?.(gesture.rect);
  }

  function end(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const { rect, from } = gesture;
    gesture = null;
    dragging = false;
    // Committed before the preview is dropped, not after: the arrangement the
    // board is holding -- who moved aside, and to where -- exists only while the
    // gesture does. Clearing first threw it away and left the dragged card on
    // top of cards that had politely stepped out of its way.
    if (rect.x !== from.x || rect.y !== from.y || rect.w !== from.w || rect.h !== from.h) {
      oncommit?.(rect);
    }
    onpreview?.(null);
  }

  /** The same gestures from the keyboard, which is the only way some people have
   * of arranging anything at all. */
  function onKeydown(event) {
    if (onremove && (event.key === "Delete" || (event.key === "Backspace" && event.altKey))) {
      // Delete, or Alt+Backspace for keyboards without one. Plain Backspace is
      // not enough: it is one stray keystroke away from losing a card.
      event.preventDefault();
      onremove();
      return;
    }
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[
      event.key
    ];
    if (!step) return;
    event.preventDefault();
    const [dx, dy] = step;
    if (event.shiftKey) {
      const [w, h] = clamp(size.w + dx, size.h + dy);
      oncommit?.({ x: at.x, y: at.y, w, h });
      return;
    }
    oncommit?.({
      x: Math.min(Math.max(at.x + dx, 0), cols - size.w),
      y: Math.min(Math.max(at.y + dy, 0), rows - size.h),
      w: size.w,
      h: size.h,
    });
  }
</script>

<ContextMenu label="{card.title ?? card.id} actions" items={menu}>
  <article
    bind:this={root}
    data-card-id={card.id}
    class="card"
    class:dragging
    class:invalid
    style="transform: translate3d({px.x}px, {px.y}px, 0); width: {px.w}px; height: {px.h}px;"
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
      <span class="size numeric">{at.w ?? size.w}×{at.h ?? size.h}</span>
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
</ContextMenu>

<style>
  .card {
    position: absolute;
    top: 0;
    left: 0;
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
    /* Displaced cards slide; the one under the finger does not (see below). */
    transition:
      transform var(--motion-fast) var(--easing-standard),
      width var(--motion-fast) var(--easing-standard),
      height var(--motion-fast) var(--easing-standard);
  }

  .card.dragging {
    /* No easing on the card being dragged: it snaps to the cell the pointer is
     * over, and interpolating that reads as lag. */
    transition: none;
    /* Lifted while it is in the air, and only then: a shadow that is always on
     * says everything is floating, which says nothing. */
    box-shadow: var(--shadow-overlay);
    z-index: var(--layer-overlay);
  }

  .card.invalid {
    border-color: var(--color-danger);
  }

  @media (prefers-reduced-motion: reduce) {
    .card {
      transition: none;
    }
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
    background: linear-gradient(
      135deg,
      transparent 0 45%,
      var(--color-border-strong) 45% 55%,
      transparent 55%
    );
  }

  .grip:hover {
    background: linear-gradient(
      135deg,
      transparent 0 45%,
      var(--color-text-muted) 45% 55%,
      transparent 55%
    );
  }
</style>
