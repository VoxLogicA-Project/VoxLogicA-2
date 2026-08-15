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
  const DEFAULT_SIDEBAR = 240;
  const MIN_SIDEBAR = 140;

  /** Whether the list is showing, and how wide. Kept here rather than in the
   * sidebar: a panel cannot be the thing that decides whether it exists. */
  let sidebar = $state(true);
  let sidebarWidth = $state(DEFAULT_SIDEBAR);
  let dragging = null;

  const clampWidth = (value) =>
    Math.min(Math.max(value, MIN_SIDEBAR), Math.max(MIN_SIDEBAR, innerWidth * 0.5));

  function startResize(event) {
    dragging = { pointerId: event.pointerId, x: event.clientX, from: sidebarWidth };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* no active pointer with this id; the drag still works, just leakier */
    }
  }

  function resize(event) {
    if (!dragging || event.pointerId !== dragging.pointerId) return;
    sidebarWidth = clampWidth(dragging.from + (event.clientX - dragging.x));
  }

  function endResize() {
    dragging = null;
  }

  /** What is wrong with the program, said in card names rather than ids.
   *
   * The ids are what the document uses to point at things; a person reading a
   * warning wants the name they gave the card. */
  const named = (id) => workspace.cards.find((card) => card.id === id)?.title ?? id;

  const problems = $derived([
    ...(workspace.issues.cycle.length
      ? [`${workspace.issues.cycle.map(named).join(" → ")} need each other`]
      : []),
    ...Object.entries(workspace.issues.duplicates).map(
      ([name, ids]) => `“${name}” is defined by ${ids.map(named).join(" and ")}`,
    ),
  ]);

  /** Cut, copy and paste for cards, through the system clipboard.
   *
   * What travels is .imgql text -- the file's own format -- so a copied card
   * can be pasted into a mail, an editor or another workspace and still be the
   * thing it was. The clipboard is asked first; a browser that refuses (no
   * permission, no secure context) falls back to a buffer in this tab, which is
   * worse only in that it does not leave the page.
   */
  let fallbackClipboard = $state("");

  async function putOnClipboard(text) {
    fallbackClipboard = text;
    try {
      await navigator.clipboard?.writeText(text);
    } catch {
      /* refused; the in-page buffer is what we have */
    }
  }

  async function takeFromClipboard() {
    try {
      const text = await navigator.clipboard?.readText();
      if (text) return text;
    } catch {
      /* refused, or nothing there */
    }
    return fallbackClipboard;
  }

  async function copyCards({ cut } = {}) {
    const ids = workspace.view.selection;
    if (!ids.length) return;
    const outcome = cut ? await board.cutCards(ids) : await board.copyCards(ids);
    if (outcome.ok && outcome.result) await putOnClipboard(outcome.result);
    if (cut) view.select([]);
  }

  async function pasteCards() {
    const text = await takeFromClipboard();
    if (!text.trim()) return;
    const outcome = await board.pasteCards(text, { page: workspace.view.page });
    if (outcome.ok && Array.isArray(outcome.result)) view.select(outcome.result);
  }

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
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
      // The chord every editor uses for this.
      event.preventDefault();
      sidebar = !sidebar;
      return;
    }
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
    const key = event.key.toLowerCase();
    if (key === "z") {
      event.preventDefault();
      if (event.shiftKey) workspaceActions.redo();
      else workspaceActions.undo();
    } else if (key === "c" && workspace.view.selection.length) {
      event.preventDefault();
      copyCards();
    } else if (key === "x" && workspace.view.selection.length) {
      event.preventDefault();
      copyCards({ cut: true });
    } else if (key === "v") {
      event.preventDefault();
      pasteCards();
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
    {#if sidebar}
      <div class="rail" style="width: {sidebarWidth}px">
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
          ondeleteproject={(name) => libraryActions.deleteProject(name)}
          oncopy={(path, project) => libraryActions.copyFile(path, project)}
        />
        <!-- The handle is the edge itself, which is where everyone reaches for
             it. Dragging sets a width; double-clicking puts it back. -->
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <div
          class="grip"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the sidebar"
          tabindex="0"
          onpointerdown={startResize}
          onpointermove={resize}
          onpointerup={endResize}
          onpointercancel={endResize}
          ondblclick={() => (sidebarWidth = DEFAULT_SIDEBAR)}
          onkeydown={(event) => {
            const step = { ArrowLeft: -16, ArrowRight: 16 }[event.key];
            if (!step) return;
            event.preventDefault();
            sidebarWidth = clampWidth(sidebarWidth + step);
          }}
        ></div>
      </div>
    {/if}

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
      oncopycards={() => copyCards()}
      oncutcards={() => copyCards({ cut: true })}
      onpastecards={() => pasteCards()}
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

  {#if problems.length}
    <!-- Facts, not errors, and not a dialogue: somebody halfway through an edit
         has a duplicate name for a few seconds. What they want is to be told
         which cards, not to be interrupted. -->
    <p class="problems" role="status">
      {#each problems as problem, index (problem)}
        {index > 0 ? " · " : ""}{problem}
      {/each}
    </p>
  {/if}

  <footer>
    <Button
      tone="quiet"
      size="sm"
      title={sidebar ? "Hide the file list (⌘B)" : "Show the file list (⌘B)"}
      onclick={() => (sidebar = !sidebar)}
    >
      {sidebar ? "◧" : "▢"}
    </Button>
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

  .problems {
    flex: none;
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    background: var(--color-danger-subtle);
    font-size: var(--text-2xs);
    color: var(--color-text);
  }

  /* The sidebar and the one file that is open. There is no third thing here,
   * which is the point: no tab strip, no breadcrumb, no second list. */
  .workbench {
    display: flex;
    gap: var(--space-3);
    flex: 1;
    min-height: 0;
  }

  .rail {
    display: flex;
    flex: none;
    min-height: 0;
  }

  .grip {
    flex: none;
    width: var(--space-2);
    margin-left: var(--space-1);
    cursor: col-resize;
    touch-action: none;
    border-radius: var(--radius-full);
  }

  .grip:hover,
  .grip:focus-visible {
    background: var(--color-border);
    outline: none;
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
