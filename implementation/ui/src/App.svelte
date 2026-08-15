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
  import { viewerFor } from "./lib/viewers/index.js";
  import BuildError from "./lib/BuildError.svelte";
  import Help from "./lib/Help.svelte";
  import Library from "./lib/Library.svelte";
  import TextEditor from "./lib/viewers/TextEditor.svelte";
  import { app } from "./lib/state.svelte.js";
  import { workspace } from "./lib/store/workspace.svelte.ts";
  import {
    board,
    card as cardActions,
    library as libraryActions,
    view,
    workspace as workspaceActions,
  } from "./lib/actions/index.ts";

  /** A reference nobody has to think about, and a name they can read.
   *
   * The id is generated and never shown; the title is what appears on the card
   * and is free to be changed, or to collide with another card's. Keeping them
   * apart is what lets a card be renamed without breaking whatever names it. */
  function nextId() {
    const taken = new Set(workspace.cards.map((card) => card.id));
    for (let n = 1; ; n += 1) {
      if (!taken.has(`c${n}`)) return `c${n}`;
    }
  }

  const TITLES = { code: "Program", result: "Result", note: "Note" };

  function titleFor(kind) {
    const base = TITLES[kind] ?? kind;
    const taken = workspace.cards.filter((card) => (card.title ?? "").startsWith(base));
    return taken.length ? `${base} ${taken.length + 1}` : base;
  }
  /** The document, or the cards it is drawn as. Tab swaps them.
   *
   * Both are the same thing seen from two distances, which is the point: a
   * board whose file you cannot read would be a board you have to trust. */
  let showing = $state("board");
  let helping = $state(false);
  /** A new project needs a name before it is a folder. Numbered rather than
   * asked for: naming a thing before making it is the dialogue this UI does not
   * have, and the name is one double-click away in the sidebar. */
  function newProjectName() {
    const taken = new Set(workspace.library.projects.map((project) => project.name));
    for (let n = 1; ; n += 1) {
      const name = n === 1 ? "Project" : `Project ${n}`;
      if (!taken.has(name)) return name;
    }
  }

  /** The card being edited, or "document" for the file itself. Editing is a
   * property of the shell rather than of a card: only one thing at a time has
   * the keyboard, and that is easier to be sure of from one place. */
  let editing = $state(null);

  function commitCard(id, text) {
    editing = null;
    const card = workspace.cards.find((entry) => entry.id === id);
    if (card && text !== (card.source ?? "")) cardActions.setSource(id, text);
  }

  /** Undo and redo belong to the workspace rather than to the board: the board
   * should not have to know that a document exists, let alone that it has a
   * file. Tab and the help sheet are the shell's too. */
  function onKeydown(event) {
    const target = event.target;
    const typing =
      target instanceof HTMLElement &&
      (target.isContentEditable || ["INPUT", "TEXTAREA"].includes(target.tagName));
    if (typing) return;

    if (editing !== null) return; // the viewer has the keyboard
    if (event.key === "Enter" && showing === "document") {
      event.preventDefault();
      editing = "document";
      return;
    }
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

  /** The bindings a program card declares, in order.
   *
   * The provisional way to find what a card produces: `let <name> =`. When the
   * engine can tell us a card's nodes this reads them from there instead, and
   * the card menu does not change. */
  function bindingsIn(source) {
    return [...(source ?? "").matchAll(/^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)/gm)].map(
      (match) => match[1],
    );
  }

  /** A card that shows something another card computes.
   *
   * It records where it came from, so renaming or moving the source changes
   * nothing: the reference is the id, and the id is not the name. */
  function derive(id, spot) {
    const source = workspace.cards.find((entry) => entry.id === id);
    if (!source || !spot) return;
    const node = bindingsIn(source.source).at(-1);
    board.deriveCard(id, nextId(), {
      kind: "result",
      node,
      title: node ? `${node}` : titleFor("result"),
      ...spot,
      page: spot.page ?? source.page,
    });
  }

  /** A copy of a card, where the board says there is room for one. */
  function duplicate(id, spot) {
    const source = workspace.cards.find((entry) => entry.id === id);
    if (!source || !spot) return;
    board.duplicateCard(id, nextId(), { ...spot, page: spot.page ?? source.page });
  }
</script>

<svelte:window onkeydown={onKeydown} />

<main>
  {#if app.buildError}
    <BuildError error={app.buildError} />
  {/if}

  <div class="workbench">
    <Library
      library={workspace.library}
      onopen={(path) => libraryActions.open(path)}
      onnewfile={(project) => libraryActions.newFile(project ?? undefined)}
      onnewproject={() => libraryActions.newProject(newProjectName())}
      onmove={(path, project) => libraryActions.moveFile(path, project)}
      onrenamefile={(path, name) => libraryActions.renameFile(path, name)}
      onrenameproject={(name, to) => libraryActions.renameProject(name, to)}
      onaddfolder={() => libraryActions.addFolder()}
      onforgetfolder={(path) => libraryActions.forgetFolder(path)}
      onreveal={(path) => libraryActions.reveal(path)}
      ondelete={(path) => libraryActions.deleteFile(path)}
    />

    <div class="pane">

  {#if !workspace.loaded}
    <p class="pending">No workspace.</p>
  {:else if showing === "document"}
    <!-- The file itself, read-only for now: seeing what the board is means
         seeing the program it writes, in the form it writes it. -->
    <section class="document" aria-label="Document">
      <!-- The same viewer a code card gets, on the whole file. Editing here and
           editing a card are the same edit written the same way, because the
           layout lives in the file's own comments. -->
      <TextEditor
        value={workspace.source}
        editing={editing === "document"}
        oncommit={(text) => {
          editing = null;
          if (text !== workspace.source) workspaceActions.setText(text);
        }}
        oncancel={() => (editing = null)}
      />
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
      onactivate={(id) => (editing = id)}
      onsendtopage={(id, page) => board.setPage(id, page)}
      onadd={(kind, x, y, w, h) =>
        board.addCard(nextId(), {
          kind,
          title: titleFor(kind),
          x,
          y,
          w,
          h,
          page: workspace.view.page,
        })}
      onremove={(id) => board.removeCard(id)}
      onduplicate={duplicate}
      onderive={derive}
      onselect={(ids) => view.select(ids)}
      selection={workspace.view.selection}
      onrename={(id, title) => cardActions.setTitle(id, title)}
    >
      {#snippet children(card)}
        {@const viewer = viewerFor(card)}
        {#if card.kind === "result" && !card.node}
          <p class="pending">Not bound to a node yet.</p>
        {:else}
          <viewer.component
            value={card.kind === "result" ? (card.node ?? "") : (card.source ?? "")}
            mono={viewer.mono}
            editing={editing === card.id && viewer.editable}
            oncommit={(text) => commitCard(card.id, text)}
            oncancel={() => (editing = null)}
          />
        {/if}
      {/snippet}
    </Bento>
  {/if}

    </div>
  </div>

  <footer>
    <!-- Naming belongs to the sidebar now: a file has its name there and a
         project has its own. What is left here is the pair of things the list
         cannot say -- where this file is on disk, and how to take it out of the
         library and into a repository. -->
    <Button tone="quiet" size="sm" onclick={() => workspaceActions.reveal()} title="Show in folder">
      ⤢
    </Button>
    <Button
      tone="quiet"
      size="sm"
      onclick={() => workspaceActions.chooseLocation()}
      title="Move this file out of the library"
    >
      Move…
    </Button>
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

  .pending {
    color: var(--color-text-subtle);
    font-size: var(--text-sm);
  }

  /* The sidebar and the one file that is open. There is no third thing here,
   * which is the point: no tab strip, no breadcrumb, no second list. */
  .workbench {
    display: flex;
    gap: var(--space-4);
    flex: 1;
    min-height: 0;
  }

  .pane {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
    min-height: 0;
  }

  .document {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--space-4);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }

  /* One line at the foot of the window: where you are, and the way to the list
   * of everything you can do. */
  footer {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .where {
    margin-left: auto;
  }


</style>
