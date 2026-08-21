<script>
  /**
   * The navigation of a selector, drawn *over* the value rather than beside it.
   *
   * A selector is not a species of card and this is not a widget with a list in
   * it: the card shows `flairs[i]`, which is a picture, and what floats on top
   * is the means of changing `i`. Put the list in the content area instead and
   * the card stops showing the thing it is a view of -- which is the whole point
   * of a card.
   *
   * Clicking edits one line of the program (`let i = 3`), so every card that
   * mentions `i` follows. There is no link between them to keep, which is why
   * this component knows nothing about any card but its own.
   */
  import { card as cardActions } from "../actions/index.ts";

  let {
    /** The card that owns the index. */
    card,
    /** How many elements the sequence has, when that is known. `null` means
     * nobody has computed it yet: walking forward stays possible, because a
     * card that refuses to move until its sequence is evaluated is a card that
     * cannot be used to evaluate it. */
    length = null,
    /** Where the walk is now. */
    at = 0,
  } = $props();

  /** Tabs are for a handful; past that they are a scrollbar with numbers on it.
   * The threshold is the point where they stop being readable at a glance. */
  const FEW = 12;

  const style = $derived(
    card.view === "tabs" || (card.view == null && length != null && length <= FEW)
      ? "tabs"
      : "chevrons",
  );

  const last = $derived(length == null ? null : Math.max(0, length - 1));

  function go(to) {
    if (to < 0) return;
    if (last != null && to > last) return;
    cardActions.setIndex(card.id, to);
  }
</script>

{#if style === "tabs" && length != null}
  <div class="tabs">
    {#each Array(length) as _, n (n)}
      <button class="tab" class:on={n === at} onclick={() => go(n)}>{n}</button>
    {/each}
  </div>
{:else}
  <button class="chevron back" disabled={at <= 0} onclick={() => go(at - 1)}>‹</button>
  <button
    class="chevron on"
    disabled={last != null && at >= last}
    onclick={() => go(at + 1)}>›</button
  >
  <span class="where">{at}{#if last != null}/{last}{/if}</span>
{/if}

<style>
  /* Every rule here sits above the picture, and none of it takes room from it:
   * a card that is mostly chrome is a card you cannot see the volume in. */
  .tabs {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    display: flex;
    gap: 0;
    padding: var(--space-1);
    overflow-x: auto;
    background: linear-gradient(var(--color-overlay), transparent);
  }

  .tab {
    flex: none;
    padding: 0 var(--space-2);
    border: 0;
    border-radius: var(--radius-sm);
    background: var(--color-surface-raised);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    cursor: pointer;
  }

  .tab.on {
    background: var(--color-accent);
    color: var(--color-surface);
  }

  .chevron {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    display: grid;
    place-items: center;
    width: var(--space-5);
    height: var(--space-7);
    border: 0;
    background: var(--color-overlay);
    color: var(--color-text);
    font-size: var(--text-md);
    cursor: pointer;
  }

  .chevron:disabled {
    opacity: 0.25;
    cursor: default;
  }

  .back {
    left: 0;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  }

  .chevron.on {
    right: 0;
    border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  }

  .where {
    position: absolute;
    left: var(--space-2);
    bottom: var(--space-1);
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }
</style>
