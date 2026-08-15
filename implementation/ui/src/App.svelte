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
  import { Bento, Button, SHORTCUTS } from "./lib/components/index.js";
  import BuildError from "./lib/BuildError.svelte";
  import Help from "./lib/Help.svelte";
  import { app } from "./lib/state.svelte.js";
  import { workspace } from "./lib/store/workspace.svelte.ts";
  import {
    board,
    card as cardActions,
    view,
    workspace as workspaceActions,
  } from "./lib/actions/index.ts";

  /** A new card needs a name before it has anything else. Short, readable in the
   * .imgql file it will be written to, and unique among what is already there. */
  function nameFor(kind) {
    const taken = new Set(workspace.cards.map((card) => card.id));
    for (let n = 1; ; n += 1) {
      const id = `${kind}${n}`;
      if (!taken.has(id)) return id;
    }
  }
  /** The document, or the cards it is drawn as. Tab swaps them.
   *
   * Both are the same thing seen from two distances, which is the point: a
   * board whose file you cannot read would be a board you have to trust. */
  let showing = $state("board");
  let helping = $state(false);

  /** Undo and redo belong to the workspace rather than to the board: the board
   * should not have to know that a document exists, let alone that it has a
   * file. Tab and the help sheet are the shell's too. */
  function onKeydown(event) {
    const target = event.target;
    const typing =
      target instanceof HTMLElement &&
      (target.isContentEditable || ["INPUT", "TEXTAREA"].includes(target.tagName));
    if (typing) return;

    if (event.key === "Tab") {
      event.preventDefault();
      showing = showing === "board" ? "document" : "board";
      return;
    }
    if (helping && event.key === "Escape") {
      helping = false;
      return;
    }
    if (!(event.metaKey || event.ctrlKey)) return;
    if (event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) workspaceActions.redo();
      else workspaceActions.undo();
    }
  }

  /** A copy of a card, where the board says there is room for one. */
  function duplicate(id, spot) {
    const source = workspace.cards.find((entry) => entry.id === id);
    if (!source || !spot) return;
    board.duplicateCard(id, nameFor(source.kind), { ...spot, page: source.page });
  }
</script>

<svelte:window onkeydown={onKeydown} />

<main>
  {#if app.buildError}
    <BuildError error={app.buildError} />
  {/if}

  {#if !workspace.loaded}
    <p class="pending">No workspace.</p>
  {:else if showing === "document"}
    <!-- The file itself, read-only for now: seeing what the board is means
         seeing the program it writes, in the form it writes it. -->
    <section class="document" aria-label="Document">
      <pre>{workspace.source}</pre>
    </section>
  {:else}
    <Bento
      cards={workspace.cards}
      cols={workspace.board.cols}
      rows={workspace.board.rows}
      page={workspace.view.page}
      zoom={workspace.view.zoom}
      label="Workspace"
      focus={workspace.view.focus}
      onarrange={(placements) => board.arrange(placements)}
      onpage={(page) => view.goToPage(page)}
      onfocus={(id) => view.focus(id)}
      onzoom={(zoom) => view.setZoom(zoom)}
      onhelp={() => (helping = true)}
      onsendtopage={(id, page) => board.setPage(id, page)}
      onadd={(kind, x, y, w, h) =>
        board.addCard(nameFor(kind), { kind, x, y, w, h, page: workspace.view.page })}
      onremove={(id) => board.removeCard(id)}
      onduplicate={duplicate}
      onselect={(ids) => view.select(ids)}
      selection={workspace.view.selection}
      onrename={(id, title) => cardActions.setTitle(id, title)}
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
  {/if}

  <footer>
    <span class="where">{showing === "document" ? "document" : "board"} · <kbd>tab</kbd></span>
    <Button tone="quiet" size="sm" onclick={() => (helping = true)} title="Shortcuts (?)">
      ?
    </Button>
  </footer>
</main>

{#if helping}
  <Help shortcuts={SHORTCUTS} onclose={() => (helping = false)} />
{/if}

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

  .document {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--space-4);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }

  .document pre {
    margin: 0;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    white-space: pre-wrap;
  }

  /* One line at the foot of the window: where you are, and the way to the list
   * of everything you can do. */
  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }
</style>
