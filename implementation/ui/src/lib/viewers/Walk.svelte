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
    /** Is there a picture under this?
     *
     * Floating the navigation over a *volume* is right: the card exists to show
     * the volume, and chrome that took a strip away from it would be chrome
     * competing with the thing it is chrome for. Floating it over *text* is just
     * two things printed in the same place -- measured, on a card whose value is
     * a number: the tabs occupied 432-457 and the value 432-451. So when there
     * is nothing to cover, it takes a line of its own instead -- and takes it in
     * the flow, since an absolute strip pinned to the bottom still collided on a
     * card three cells tall.
     */
    floating = true,
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

<div class="walk" class:floating class:strip={!floating}>
  {#if style === "tabs" && length != null}
    <div class="tabs">
      {#each Array(length) as _, n (n)}
        <button class="tab" class:on={n === at} onclick={() => go(n)}>{n}</button>
      {/each}
    </div>
  {:else}
    <button class="chevron back" disabled={at <= 0} onclick={() => go(at - 1)}>‹</button>
    <button
      class="chevron next"
      disabled={last != null && at >= last}
      onclick={() => go(at + 1)}>›</button
    >
    <span class="where">{at}{#if last != null}/{last}{/if}</span>
  {/if}
</div>

<style>
  /* Over a picture: absolute, and taking no room from it -- a card that is
   * mostly chrome is a card you cannot see the volume in. */
  .floating {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }

  .floating > * {
    pointer-events: auto;
  }

  /* Over anything else: a line of its own, in the flow. Not absolute -- the
   * point is that the content box is shorter by exactly this much, so there is
   * no arrangement of card size and content in which the two can land on each
   * other. */
  .strip {
    flex: none;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding-top: var(--space-1);
  }

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

  /* In a strip the tabs are simply the strip's content. */
  .strip .tabs {
    position: static;
    padding: 0;
    background: none;
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

  .next {
    right: 0;
    border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  }

  .strip .chevron {
    position: static;
    transform: none;
    width: var(--space-5);
    height: var(--space-5);
    border-radius: var(--radius-sm);
  }

  .where {
    position: absolute;
    left: var(--space-2);
    bottom: var(--space-1);
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .strip .where {
    position: static;
  }
</style>
