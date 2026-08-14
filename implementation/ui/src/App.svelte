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

  /** A new card needs a name before it has anything else. Short, readable in the
   * .imgql file it will be written to, and unique among what is already there. */
  function nameFor(kind) {
    const taken = new Set(workspace.cards.map((card) => card.id));
    for (let n = 1; ; n += 1) {
      const id = `${kind}${n}`;
      if (!taken.has(id)) return id;
    }
  }
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
      onarrange={(placements) => board.arrange(placements)}
      onpage={(page) => view.goToPage(page)}
      onadd={(kind, x, y, w, h) =>
        board.addCard(nameFor(kind), { kind, x, y, w, h, page: workspace.view.page })}
      onremove={(id) => board.removeCard(id)}
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
    gap: var(--space-3);
    /* The board is the application: it gets the window, less its own margin and
     * whatever the pager needs. `dvh` and not `vh` so a mobile browser's
     * disappearing toolbar does not leave a strip of board underneath it. */
    height: 100dvh;
    padding: var(--space-3);
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
