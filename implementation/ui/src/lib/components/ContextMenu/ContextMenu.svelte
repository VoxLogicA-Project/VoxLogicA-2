<script>
  /**
   * A menu of actions for the thing you pointed at.
   *
   * Wraps the region it belongs to, so the menu and its target cannot drift
   * apart. What makes this a component rather than a styled list is everything
   * a hand-rolled menu forgets: Escape closes it, arrows walk it, Home/End jump,
   * Shift+F10 opens it from the keyboard, a click anywhere else dismisses it,
   * focus returns to where it came from, and the panel is clamped into the
   * viewport instead of hanging off the edge.
   */
  let { items = [], label = "Actions", children } = $props();

  let open = $state(false);
  let position = $state({ x: 0, y: 0 });
  let activeIndex = $state(-1);
  let panel = $state(null);
  let anchor = $state(null);
  let returnFocusTo = null;

  /** Indices of items that can actually be moved to (separators cannot). */
  const stops = $derived(
    items.map((item, index) => ({ item, index })).filter(({ item }) => !item.separator),
  );

  function openAt(x, y) {
    returnFocusTo = document.activeElement;
    position = { x, y };
    activeIndex = -1;
    open = true;
  }

  function close({ restoreFocus = true } = {}) {
    if (!open) return;
    open = false;
    activeIndex = -1;
    if (restoreFocus && returnFocusTo?.focus) returnFocusTo.focus();
    returnFocusTo = null;
  }

  function choose(item) {
    if (item.disabled) return;
    close();
    item.onselect?.();
  }

  function onContextMenu(event) {
    event.preventDefault();
    // The innermost menu wins. Without this, right-clicking a card inside a
    // board would open the card's menu and then the board's on top of it.
    event.stopPropagation();
    openAt(event.clientX, event.clientY);
  }

  function onAnchorKeydown(event) {
    // The platform gesture for "context menu, without a mouse".
    if (event.key !== "F10" || !event.shiftKey) return;
    event.preventDefault();
    // Anchor to whatever has focus -- that is the thing the keyboard user is
    // "pointing at". The wrapper itself is display:contents and has no box.
    const origin = anchor?.contains(document.activeElement)
      ? document.activeElement
      : anchor?.firstElementChild;
    const box = origin?.getBoundingClientRect();
    openAt(box ? box.left : window.innerWidth / 2, box ? box.bottom : window.innerHeight / 2);
  }

  function step(delta) {
    if (stops.length === 0) return;
    const current = stops.findIndex(({ index }) => index === activeIndex);
    const next = current === -1
      ? (delta > 0 ? 0 : stops.length - 1)
      : (current + delta + stops.length) % stops.length;
    activeIndex = stops[next].index;
  }

  function onMenuKeydown(event) {
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        close();
        break;
      case "ArrowDown":
        event.preventDefault();
        step(1);
        break;
      case "ArrowUp":
        event.preventDefault();
        step(-1);
        break;
      case "Home":
        event.preventDefault();
        if (stops.length) activeIndex = stops[0].index;
        break;
      case "End":
        event.preventDefault();
        if (stops.length) activeIndex = stops[stops.length - 1].index;
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (activeIndex >= 0) choose(items[activeIndex]);
        break;
      case "Tab":
        // Tabbing out of an open menu means "I am done here".
        close();
        break;
    }
  }

  // Clamp into the viewport once the panel has a measurable size. Doing this
  // after paint rather than guessing the size keeps it correct for any item
  // list, including one long enough to be taller than the window.
  $effect(() => {
    if (!open || !panel) return;
    const margin = 8;
    const box = panel.getBoundingClientRect();
    let { x, y } = position;
    if (x + box.width + margin > window.innerWidth) x = window.innerWidth - box.width - margin;
    if (y + box.height + margin > window.innerHeight) y = window.innerHeight - box.height - margin;
    x = Math.max(margin, x);
    y = Math.max(margin, y);
    if (x !== position.x || y !== position.y) position = { x, y };
  });

  // Real DOM focus follows activeIndex, so screen readers and :focus-visible
  // agree with the highlight instead of only looking right.
  $effect(() => {
    if (!open || activeIndex < 0 || !panel) return;
    panel.querySelector(`[data-index="${activeIndex}"]`)?.focus();
  });

  $effect(() => {
    if (open && activeIndex < 0) panel?.focus();
  });
</script>

<svelte:window
  onresize={() => close({ restoreFocus: false })}
  onblur={() => close({ restoreFocus: false })}
/>

<!-- The wrapper is a passive region: it owns no behaviour of its own beyond
     "right-clicking in here opens my menu", which is exactly a context menu. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  bind:this={anchor}
  class="anchor"
  oncontextmenu={onContextMenu}
  onkeydown={onAnchorKeydown}
>
  {@render children?.()}
</div>

{#if open}
  <!-- Pointer-down, not click: dismissal should happen on press, before the
       press can also activate whatever is underneath. -->
  <div
    class="scrim"
    onpointerdown={() => close()}
    oncontextmenu={(event) => {
      event.preventDefault();
      close();
    }}
    role="presentation"
  ></div>

  <div
    bind:this={panel}
    class="panel"
    role="menu"
    aria-label={label}
    tabindex="-1"
    style="left: {position.x}px; top: {position.y}px;"
    onkeydown={onMenuKeydown}
  >
    {#each items as item, index (index)}
      {#if item.separator}
        <div class="separator" role="separator"></div>
      {:else}
        <button
          type="button"
          role="menuitem"
          class="item"
          class:danger={item.danger}
          data-index={index}
          tabindex={activeIndex === index ? 0 : -1}
          disabled={item.disabled}
          onclick={() => choose(item)}
          onmouseenter={() => (activeIndex = index)}
        >
          <span class="item-label">{item.label}</span>
          {#if item.hint}<span class="hint">{item.hint}</span>{/if}
        </button>
      {/if}
    {/each}
  </div>
{/if}

<style>
  .anchor {
    display: contents;
  }

  .scrim {
    position: fixed;
    inset: 0;
    z-index: var(--layer-overlay);
  }

  .panel {
    position: fixed;
    z-index: calc(var(--layer-overlay) + 1);
    min-width: 180px;
    padding: var(--space-1);
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    border-radius: var(--radius-md);
    /* The one place a real shadow is earned: this panel floats above unrelated
     * content and needs to be read as detached from it. */
    box-shadow: var(--shadow-overlay);
  }

  .item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    width: 100%;
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    font-size: var(--text-sm);
    text-align: left;
    color: var(--color-text);
  }

  .item:hover:not(:disabled),
  .item:focus-visible {
    background: var(--color-surface-hover);
  }

  .item.danger {
    color: var(--color-danger);
  }

  .item.danger:hover:not(:disabled) {
    background: var(--color-danger-subtle);
  }

  .item:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .item-label {
    white-space: nowrap;
  }

  .hint {
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
    font-variant-numeric: tabular-nums;
  }

  .separator {
    height: var(--border-width);
    margin: var(--space-1) var(--space-1);
    background: var(--color-border);
  }
</style>
