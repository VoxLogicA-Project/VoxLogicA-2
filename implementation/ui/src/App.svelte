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
  import SourceEditor from "./lib/source/SourceEditor.svelte";
  import { INTERACTION } from "./lib/source/interaction.js";
  import ResultSubscription from "./lib/viewers/ResultSubscription.svelte";
  import ResultState from "./lib/viewers/ResultState.svelte";
  import { app } from "./lib/state.svelte.js";
  import { workspace } from "./lib/store/workspace.svelte.ts";
  import { results } from "./lib/store/results.svelte.ts";
  import {
    board,
    card as cardActions,
    library as libraryActions,
    resultsActions,
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
  const showing = $derived(workspace.view.showing ?? "board");
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
    // First: until the program parses, nothing else here is worth reading, and
    // every name is unresolved for the same one reason.
    ...(workspace.issues.compile
      ? [
          workspace.issues.compile.line
            ? `line ${workspace.issues.compile.line}: ${workspace.issues.compile.message}`
            : workspace.issues.compile.message,
        ]
      : []),
    ...(workspace.issues.cycle.length
      ? [`${workspace.issues.cycle.map(named).join(" → ")} need each other`]
      : []),
    ...Object.entries(workspace.issues.duplicates).map(
      ([name, ids]) => `“${name}” is defined by ${ids.map(named).join(" and ")}`,
    ),
    // Said in card names, like the rest: "big overlaps one" means nothing to
    // somebody who never sees an id.
    ...(workspace.issues.overlaps ?? []).map(
      ([first, second]) => `${named(first)} and ${named(second)} are on the same cells`,
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

  /** The selection as .imgql, kept ready for a drag that has not started yet.
   *
   * A DataTransfer can only be filled inside `dragstart`, and by then there is
   * no time to ask the server anything. So the text is fetched when the
   * selection changes -- through `board.copyCards`, the same action the
   * clipboard uses, so a dragged card and a copied card are the same bytes by
   * construction rather than by two implementations agreeing. Reading is not an
   * edit: `copyCards` changes nothing and costs one message.
   */
  let cardsText = $state(null);

  $effect(() => {
    const ids = [...workspace.view.selection];
    if (!ids.length) {
      cardsText = null;
      return;
    }
    let current = true;
    board.copyCards(ids).then((outcome) => {
      // A selection that changed while we were asking is not this one.
      if (current && outcome.ok && outcome.result) cardsText = { ids, text: outcome.result };
    });
    return () => (current = false);
  });

  /** Cards dropped on a row of the sidebar: they go into that file.
   *
   * Moving is cutting and pasting, in that order, and for the same reason it is
   * in that order at the clipboard: the text the target receives has to be the
   * text the board actually gave up. Alt copies instead, so the cards stay.
   */
  async function dropCardsInLibrary(target, payload) {
    let text = payload.text;
    if (!payload.copy && payload.ids.length) {
      const cut = await board.cutCards(payload.ids);
      if (!cut.ok || !cut.result) return;
      text = cut.result;
    }
    let path = target.path;
    if (!path) {
      // A project is a folder, so cards dropped on one need a file to be in.
      // Making it is the only thing that could have been meant, and it is the
      // same file `+` would have made.
      const made = await libraryActions.newFile(target.project ?? undefined);
      if (!made.ok || !made.result) return;
      path = made.result;
    }
    await libraryActions.pasteCards(path, text);
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
      view.show(showing === "board" ? "document" : "board");
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
    } else if (key === "l") {
      // mod+L, never a bare letter: the letters belong to whoever is typing
      // into a card, and that rule has no exceptions.
      event.preventDefault();
      if (event.shiftKey) cycleCardLens();
      else cycleLens();
    }
  }

  /** What the current selection in an editor is, once the server has said.
   *
   * Debounced, because a selection changes as fast as a pointer moves and each
   * answer costs the reducer a compile. The reply carries the state as well as
   * the hash, so highlighting three words answers "is this already computed?"
   * in one round trip rather than two.
   */
  let probe = $state(null);
  let probeTimer = null;

  function probeSelection(selection) {
    clearTimeout(probeTimer);
    const text = (selection?.text ?? "").trim();
    if (!text) {
      probe = null;
      return;
    }
    probeTimer = setTimeout(async () => {
      const outcome = await resultsActions.hashOf(text);
      // A selection that moved on while we were asking is not this one.
      if (outcome.ok) probe = outcome.result ? { text, ...outcome.result } : null;
    }, 220);
  }

  /** What a card shows. Named as the thing it does, in words.
   *
   * It was three glyphs and a tooltip about "how close you stand", which is a
   * metaphor the person using this has no reason to hold: a control whose
   * meaning arrives only on hover is a control nobody uses on purpose. */
  const LENSES = ["source", "both", "value"];
  const LENS_WORD = { source: "code", both: "code + value", value: "value" };

  function cycleLens() {
    const at = LENSES.indexOf(workspace.view.lens);
    view.setLens(LENSES[(at + 1) % LENSES.length]);
  }

  /** The selected card's own answer, cycled -- including back to "follow the
   * board", because an override with no way back is a decision you cannot undo
   * by the same means you made it.
   *
   * One chord for four choices rather than four chords: they are positions in
   * one setting, and a keyboard that needed a key per position would be
   * teaching the wrong shape. */
  const CARD_LENSES = ["", ...LENSES];

  function cycleCardLens() {
    for (const id of workspace.view.selection) {
      const card = workspace.cards.find((entry) => entry.id === id);
      const at = CARD_LENSES.indexOf(card?.view ?? "");
      cardActions.setViewMode(id, CARD_LENSES[(at + 1) % CARD_LENSES.length]);
    }
  }

  /** How far back to stand from this card.
   *
   * The card's own answer if it has one, the board's otherwise. Board-wide by
   * default because twenty cards each sitting in a mode somebody set once is a
   * board you cannot read at a glance; overridable because a volume wants
   * `value` while the code beside it wants `source`, and forcing one answer on
   * both would make the lens useless. */
  function lensFor(card) {
    return card.view || workspace.view.lens || "both";
  }

  /** The cards with something queued or computing right now.
   *
   * Derived, never assigned: a card is running because one of the nodes it is
   * about is, and that is a fact about the results store rather than a flag
   * somebody has to remember to clear. Press Run on B and A lights up too --
   * nothing arranges that, B's dependencies simply *are* A's bindings, and both
   * resolve to the same hashes.
   */
  const running = $derived(
    workspace.cards
      .filter((card) =>
        nodesOf(card).some((node) => {
          const state = results.get(results.hashFor(node)).state;
          return state === "pending" || state === "computing";
        }),
      )
      .map((card) => card.id),
  );

  /** Every name a card is about: what it declares, plus what it is bound to. */
  function nodesOf(card) {
    const names = card.kind === "code" ? bindingsIn(card.source) : [];
    return card.node ? [...names, card.node] : names;
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

<main data-interaction={INTERACTION}>
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
          onaddlabel={(path, label) => libraryActions.addLabel(path, label)}
          onremovelabel={(path, label) => libraryActions.removeLabel(path, label)}
          onpastecards={dropCardsInLibrary}
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
    <p class="pending">Connecting…</p>
  {:else if !workspace.path}
    <!-- Nothing open, and nothing invented. The application used to create a
         file named after the minute at every launch, so a week of opening it
         left a week of empty documents. An empty state that offers is better
         than a file nobody asked for. -->
    <section class="welcome">
      <h1>VoxLogicA</h1>
      <p>Nothing is open. Start something, or pick a file from the list.</p>
      <Button onclick={() => libraryActions.newFile()}>New program</Button>
    </section>
  {:else if showing === "document"}
    <!-- The file itself, read-only for now: seeing what the board is means
         seeing the program it writes, in the form it writes it. -->
    <section class="document" aria-label="Document">
      <!-- The same viewer a code card gets, on the whole file. Editing here and
           editing a card are the same edit written the same way, because the
           layout lives in the file's own comments. -->
      <!-- The same surface a code card gets, on the whole file: one
           highlighter, two places to look at it from. -->
      <SourceEditor
        value={workspace.source}
        bindings={workspace.nodes}
        stateOf={(hash) => results.get(hash).state}
        onselect={probeSelection}
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
      onmeasured={(id, w, h) => board.measured(id, w, h)}
      onrun={(id) => cardActions.run(id)}
      onlens={(id, lens) => cardActions.setViewMode(id, lens)}
      onsavethis={(id) => cardActions.saveThis(id)}
      onprintthis={(id) => cardActions.printThis(id)}
      onfocusbinding={(id, name) => cardActions.setFocus(id, name)}
      bindingsOf={(card) => (card.kind === "code" ? bindingsIn(card.source) : [])}
      {running}
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
      {cardsText}
      onrename={(id, title) => cardActions.setTitle(id, title)}
    >
      {#snippet children(card)}
        {@const result = results.forCard(card)}
        {@const viewer = viewerFor(card, result)}
        {#if viewer.result && !card.node}
          <!-- A print or save whose expression is not a bare name has nothing to
               resolve to a hash yet; it says what it is a view of instead. -->
          <p class="pending">{card.expression ?? "Not bound to a node yet."}</p>
        {:else if viewer.result}
          <!-- Subscribed for as long as it is on screen, and only that long:
               the server pushes updates for hashes somebody is looking at. -->
          <ResultSubscription node={card.node} />
          <viewer.component {result} node={card.node ?? ""} />
        {:else if viewer.source}
          {@const lens = lensFor(card)}
          <!-- One movement, three distances. The program and its values are the
               same object seen from further back or closer in, so `both` is the
               middle rather than a third mode: at either end nothing is hidden
               that the other end would have shown for free. -->
          <div class="lensed" data-lens={lens}>
            {#if lens !== "value"}
              <viewer.component
                value={card.source ?? ""}
                bindings={workspace.nodes}
                stateOf={(hash) => results.get(hash).state}
                editing={editing === card.id}
                onselect={probeSelection}
                oncommit={(text) => commitCard(card.id, text)}
                oncancel={() => (editing = null)}
              />
            {/if}
            {#if lens !== "source" && card.focus}
              {@const hash = results.hashFor(card.focus)}
              {@const shown = results.get(hash)}
              {@const how = viewerFor(card, shown)}
              <ResultSubscription node={card.focus} />
              <div class="value" class:only={lens === "value"}>
                {#if how.bytes && shown.state === "done"}
                  <!-- The bytes themselves, and only once the node is done:
                       the URL is the hash, so it is immutable and cacheable. -->
                  <how.component layers={[{ hash, url: `/api/node/${hash}` }]} />
                {:else}
                  <ResultState result={shown} node={card.focus} />
                {/if}
              </div>
            {/if}
          </div>
        {:else}
          <viewer.component
            value={card.source ?? ""}
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
      {#if workspace.issues.overlaps?.length}
        <!-- The one problem the board can fix by itself, so it offers to. A
             fact with nothing to do about it is a fact that gets ignored. -->
        <Button tone="quiet" size="sm" onclick={() => board.untangle()}>
          Move them apart
        </Button>
      {/if}
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
    <!-- One control for the whole board, because the lens is a place to stand
         and not a per-card preference. A card that wants otherwise says so
         itself, and then stops following this. -->
    <Button
      tone="quiet"
      size="sm"
      onclick={cycleLens}
      title="What every card shows — click for the next one (⌘L)"
    >
      all cards: {LENS_WORD[workspace.view.lens] ?? "code + value"}
    </Button>
    {#if probe}
      <!-- The editor as a probe into the store: what you highlighted, and
           whether this system has worked it out before. Said quietly, in the
           footer, because it answers a question you asked with a gesture. -->
      <span class="probe" title={probe.hash}>
        <code>{probe.text.length > 28 ? probe.text.slice(0, 28) + "…" : probe.text}</code>
        {probe.state === "done" ? "computed" : probe.state}
      </span>
    {/if}
    <!-- What Tab does, said as what it does. "board · tab" was a label only
         somebody who already knew could read, and it was being covered by the
         dev button besides. -->
    <Button
      tone="quiet"
      size="sm"
      onclick={() => view.show(showing === "board" ? "document" : "board")}
      title="Tab"
    >
      {showing === "document" ? "showing the file" : "showing the cards"}
    </Button>
    <span class="spacer"></span>
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

  .probe {
    display: flex;
    align-items: baseline;
    gap: var(--space-1);
    min-width: 0;
    color: var(--color-text-muted);
    font-size: var(--text-2xs);
    white-space: nowrap;
  }

  .probe code {
    overflow: hidden;
    max-width: 16ch;
    color: var(--color-text);
    font-family: var(--font-mono);
    text-overflow: ellipsis;
  }

  /* The whole pane, because there is nothing else in it -- and centred, because
     a first screen with one thing on it should put that thing where the eye
     already is. */
  .welcome {
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    text-align: center;
  }

  .welcome h1 {
    margin: 0;
    font-size: var(--text-2xl);
    font-weight: var(--weight-medium);
    letter-spacing: var(--tracking-tight, normal);
  }

  .welcome p {
    margin: 0;
    max-width: 34ch;
    color: var(--color-text-muted);
    font-size: var(--text-sm);
  }

  .pending {
    color: var(--color-text-subtle);
    font-size: var(--text-sm);
  }

  /* The two surfaces stacked, with the program taking whatever the value does
     not. At `source` there is one child and at `value` there is one child, so
     the same rule draws all three distances without a branch per lens. */
  .lensed {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    width: 100%;
    /* `min-height`, not `height`: the card's content area is a plain block with
       no definite height of its own, so a percentage *height* here resolves
       against nothing and collapses -- taking both surfaces with it. Asking to
       be at least as tall works whether the parent is definite or not. */
    min-height: 100%;
  }

  /* The value never takes more than half of a card it is sharing: a program you
     can no longer read is a program you cannot fix, and the value is the thing
     you can always get more of by standing further back. */
  .value {
    flex: none;
    max-height: 50%;
    min-height: 0;
    overflow: auto;
    padding-top: var(--space-2);
    border-top: 1px solid var(--color-border);
  }

  /* Alone, it is the card. */
  .value.only {
    flex: 1;
    max-height: none;
    padding-top: 0;
    border-top: none;
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

  /* Pushes whatever follows to the far end, without being a thing itself.
     `margin-left: auto` on a label meant the label had to exist to do it, and
     it was the label that had nothing useful to say. */
  .spacer {
    flex: 1;
  }

  /* Nothing in the footer may be squeezed into ellipsis by its neighbours: a
     control whose name is cut in half is a control nobody can learn. */
  footer :global(button) {
    flex: none;
    white-space: nowrap;
  }


</style>
