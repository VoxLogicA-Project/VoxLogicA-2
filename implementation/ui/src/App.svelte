<script>
  /**
   * The application shell: the workspace, drawn.
   *
   * Composition and nothing else. It reads the replica and calls actions; it
   * holds no state of its own and assigns to none, which is the rule the whole
   * store design rests on -- every change to a workspace has a name, and that
   * name is available to a person in a browser and to an agent over MCP alike.
   *
   * The build-error overlay is not workspace content: it is the page telling you
   * why it stopped updating, and it has to appear wherever you are.
   */
  import { Bento } from "./lib/components/index.js";
  import BuildError from "./lib/BuildError.svelte";
  import { app } from "./lib/state.svelte.js";
  import { workspace } from "./lib/store/workspace.svelte.ts";
  import { board, view } from "./lib/actions/index.ts";
</script>

<main>
  {#if app.buildError}
    <BuildError error={app.buildError} />
  {/if}

  {#if workspace.loaded}
    <Bento
      cards={workspace.cards}
      cols={workspace.board.cols}
      rows={workspace.board.rows}
      page={workspace.view.page}
      zoom={workspace.view.zoom}
      label="Workspace"
      onmove={(id, x, y) => board.moveCard(id, x, y)}
      onresize={(id, w, h) => board.resizeCard(id, w, h)}
      onpage={(page) => view.goToPage(page)}
    >
      {#snippet children(card)}
        {#if card.kind === "code"}
          <pre class="source">{card.source ?? ""}</pre>
        {:else if card.kind === "result"}
          <p class="pending">{card.node ?? "no node"} — results are not wired up yet</p>
        {:else}
          <p class="note">{card.source ?? ""}</p>
        {/if}
      {/snippet}
    </Bento>
  {:else}
    <p class="pending">No workspace.</p>
  {/if}
</main>

<style>
  main {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    padding: var(--space-5);
  }

  .source {
    margin: 0;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    white-space: pre;
  }

  .note {
    font-size: var(--text-sm);
  }

  .pending {
    color: var(--color-text-subtle);
    font-size: var(--text-sm);
  }
</style>
