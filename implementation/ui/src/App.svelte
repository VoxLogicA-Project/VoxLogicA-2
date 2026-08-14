<script>
  /**
   * The application shell: a bento board and nothing else yet.
   *
   * The board is the frame the real UI is being built in -- cards that hold a
   * clickable program, a node's state, a result rendered from its hash. None of
   * those exist yet, so what is on it today is scaffolding, and it is labelled
   * as such rather than dressed up as a product.
   *
   * The build-error overlay is not board content: it is the page telling you why
   * it stopped updating, and it has to appear wherever you are.
   */
  import { Bento } from "./lib/components/index.js";
  import BuildError from "./lib/BuildError.svelte";
  import { app } from "./lib/state.svelte.js";

  // Placeholder cards. They exist so the board can be used and inspected before
  // the polymorphic card content lands; delete them the day it does.
  const cards = [
    { id: "program", title: "program", x: 0, y: 0, w: 5, h: 4 },
    { id: "results", title: "results", x: 5, y: 0, w: 4, h: 6, minW: 3 },
    { id: "notes", title: "notes", x: 0, y: 4, auto: true, maxW: 5 },
  ];

  const placeholder = {
    program: "A clickable program will live here.",
    results: "A result, addressed by node hash, rendered from the store.",
    notes:
      "Auto-sized: no width or height was given, so this card took the cells its content needed.",
  };
</script>

<main>
  {#if app.buildError}
    <BuildError error={app.buildError} />
  {/if}

  <Bento {cards} cols={9} rows={8} label="Workspace">
    {#snippet children(card)}
      <p class="placeholder">{placeholder[card.id]}</p>
    {/snippet}
  </Bento>
</main>

<style>
  main {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    padding: var(--space-5);
  }

  .placeholder {
    color: var(--color-text-subtle);
    font-size: var(--text-sm);
  }
</style>
