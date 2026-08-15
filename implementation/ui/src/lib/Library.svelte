<script>
  /**
   * The sidebar: every file there is, and which one is open.
   *
   * This *is* the tab bar. One file shows in the pane at a time and this list is
   * how you reach another, so there is no strip of tabs -- a tab strip is a
   * second copy of this list, in a different order and truncated, and it is
   * where "which of these nine is the one I mean" comes from.
   *
   * Everything a person needs to *find* something is therefore here rather than
   * anywhere else: a filter, an order, and a shape that says what a thing is
   * before you have read its name. Projects are folders and files are files,
   * which is why the icons are a folder and a page and why dragging is literal.
   */
  import { Button, ContextMenu } from "./components/index.js";

  let {
    library = { root: "", projects: [], files: [] },
    onopen,
    onnewfile,
    onnewproject,
    onaddfolder,
    onforgetfolder,
    onmove,
    onrenamefile,
    onrenameproject,
    onreveal,
    ondelete,
    ondeleteproject,
    oncopy,
  } = $props();

  const ORDERS = [
    { id: "name", label: "Name" },
    { id: "recent", label: "Last changed" },
  ];

  let filter = $state("");
  let order = $state("name");
  /** Projects the user has folded away, by name. */
  let folded = $state(new Set());
  /** `{kind: "file" | "project", key}` while something is being renamed. */
  let renaming = $state(null);
  let draft = $state("");
  /** The project a dragged file is over: a name, "" for the top, or null. */
  let over = $state(null);
  /** Paths the user has picked out. The open file is not necessarily among
   * them: opening is looking, selecting is choosing what to act on. */
  let picked = $state(new Set());
  /** Where a shift-range starts from. */
  let anchor = $state(null);
  /** Paths waiting for a second confirmation before they are deleted. */
  let armed = $state(null);

  /** The cut buffer: what was picked up, and whether it is moving or copying.
   *
   * A cut does not remove anything yet -- nothing is gone until it lands
   * somewhere. Files marked for a move show dimmed until they are pasted or
   * the buffer is dropped, which is the only honest way to draw "this is on its
   * way out but still here". */
  let buffer = $state(null);

  function pickUp(paths, mode) {
    buffer = paths.length ? { paths, mode } : null;
  }

  /** Where a paste lands when nothing else says: beside the file being looked
   * at, which is where somebody who just chose a file expects it to go. */
  const here = $derived(library.files.find((file) => file.open)?.project ?? null);

  function paste(project = here) {
    const held = buffer;
    if (!held) return;
    for (const path of held.paths) {
      if (held.mode === "cut") onmove?.(path, project);
      else oncopy?.(path, project);
    }
    // A cut is spent once it lands; a copy can be pasted again, which is what
    // everyone expects of a copy.
    if (held.mode === "cut") buffer = null;
  }

  const needle = $derived(filter.trim().toLowerCase());

  function sorted(files) {
    const rows = [...files];
    if (order === "recent") rows.sort((a, b) => b.modified - a.modified);
    else rows.sort((a, b) => a.name.localeCompare(b.name));
    return rows;
  }

  function within(project) {
    return sorted(
      library.files.filter(
        (file) => file.project === project && (!needle || file.name.toLowerCase().includes(needle)),
      ),
    );
  }

  const loose = $derived(within(null));
  /** While filtering, a project with no match is not in the way. */
  const shown = $derived(
    library.projects.filter((project) => !needle || within(project.name).length > 0),
  );
  const matches = $derived(
    needle ? library.files.filter((file) => file.name.toLowerCase().includes(needle)).length : null,
  );

  /** Every visible row, in the order they appear: what shift-click walks. */
  const flat = $derived([
    ...loose,
    ...shown.flatMap((project) => (folded.has(project.name) ? [] : within(project.name))),
  ]);

  function choose(file, event) {
    const additive = event.metaKey || event.ctrlKey;
    const ranged = event.shiftKey;
    if (ranged && anchor) {
      const order = flat.map((entry) => entry.path);
      const from = order.indexOf(anchor);
      const to = order.indexOf(file.path);
      if (from !== -1 && to !== -1) {
        const [start, end] = from < to ? [from, to] : [to, from];
        picked = new Set(order.slice(start, end + 1));
        return;
      }
    }
    if (additive) {
      const next = new Set(picked);
      if (next.has(file.path)) next.delete(file.path);
      else next.add(file.path);
      picked = next;
      anchor = file.path;
      return;
    }
    picked = new Set([file.path]);
    anchor = file.path;
    // A plain click is also how you open something: choosing one file and
    // looking at it are the same gesture, and always were.
    onopen?.(file.path);
  }

  /** What an action on `file` applies to: the selection if it is in it. */
  function scope(file) {
    return picked.has(file.path) && picked.size > 1 ? [...picked] : [file.path];
  }

  /** What is waiting for a second confirmation: files, or one empty project. */
  function arm(paths) {
    armed = { kind: "files", paths };
  }

  function armProject(name) {
    armed = { kind: "project", name };
  }

  function confirmDelete() {
    const target = armed;
    armed = null;
    if (!target) return;
    if (target.kind === "project") {
      ondeleteproject?.(target.name);
      return;
    }
    for (const path of target.paths) ondelete?.(path);
    picked = new Set([...picked].filter((path) => !target.paths.includes(path)));
  }

  function fold(name) {
    const next = new Set(folded);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    folded = next;
  }

  function startRename(kind, key, current) {
    renaming = { kind, key };
    draft = current;
  }

  function commitRename() {
    const target = renaming;
    const text = draft.trim();
    renaming = null;
    if (!target || !text) return;
    if (target.kind === "file") onrenamefile?.(target.key, text);
    else onrenameproject?.(target.key, text);
  }

  function onDrop(event, project) {
    event.preventDefault();
    over = null;
    const payload = event.dataTransfer?.getData("text/voxlogica-file");
    const copying = event.dataTransfer?.getData("text/voxlogica-mode") === "copy" || event.altKey;
    for (const path of (payload ?? "").split("\n").filter(Boolean)) {
      if (copying) oncopy?.(path, project);
      else onmove?.(path, project);
    }
  }

  function fileMenu(file) {
    const paths = scope(file);
    const many = paths.length > 1 ? ` (${paths.length})` : "";
    return [
      {
        label: "Rename",
        hint: "F2",
        disabled: paths.length > 1,
        onselect: () => startRename("file", file.path, file.name),
      },
      { label: "Show in folder", onselect: () => onreveal?.(file.path) },
      { separator: true },
      ...(file.project === null
        ? []
        : [
            {
              label: `Move to the top${many}`,
              onselect: () => paths.forEach((path) => onmove?.(path, null)),
            },
          ]),
      ...library.projects
        .filter((project) => project.name !== file.project)
        .map((project) => ({
          label: `Move to ${project.name}${many}`,
          onselect: () => paths.forEach((path) => onmove?.(path, project.name)),
        })),
      { separator: true },
      { label: `Cut${many}`, hint: "mod+X", onselect: () => pickUp(paths, "cut") },
      { label: `Copy${many}`, hint: "mod+C", onselect: () => pickUp(paths, "copy") },
      {
        label: "Paste here",
        hint: "mod+V",
        disabled: buffer === null,
        onselect: () => paste(file.project),
      },
      { separator: true },
      // Armed, not done: the menu asks, the bar at the top confirms. A delete
      // that happened on one click of a menu nobody opened deliberately is the
      // one mistake this list can make that cannot be undone.
      { label: `Delete${many}…`, danger: true, onselect: () => arm(paths) },
    ];
  }

  function projectMenu(project) {
    const holds = library.files.some((file) => file.project === project.name);
    return [
      {
        label: "Rename",
        disabled: project.linked,
        hint: project.linked ? "linked folders keep their own name" : "double-click",
        onselect: () => startRename("project", project.name, project.name),
      },
      { label: "New file here", onselect: () => onnewfile?.(project.name) },
      {
        label: buffer === null ? "Paste here" : `Paste ${buffer.paths.length} here`,
        hint: "mod+V",
        disabled: buffer === null,
        onselect: () => paste(project.name),
      },
      { label: "Show in folder", onselect: () => onreveal?.(project.path) },
      { separator: true },
      ...(project.linked
        ? [
            {
              label: "Remove from the list",
              hint: "the folder itself is untouched",
              onselect: () => onforgetfolder?.(project.path),
            },
          ]
        : [
            {
              label: "Delete project…",
              // Empty ones only. Deleting a project that still holds files
              // would be deleting the files, which the list already does
              // explicitly, one at a time, with the same confirmation.
              hint: holds ? "only when it is empty" : undefined,
              disabled: holds,
              danger: true,
              onselect: () => armProject(project.name),
            },
          ]),
    ];
  }
</script>

{#snippet fileIcon()}
  <svg class="icon" viewBox="0 0 16 16" aria-hidden="true">
    <path
      d="M4 1.5h5L12.5 5v9.5h-8.5z"
      fill="none"
      stroke="currentColor"
      stroke-width="1.2"
      stroke-linejoin="round"
    />
    <path d="M9 1.5V5h3.5" fill="none" stroke="currentColor" stroke-width="1.2" />
  </svg>
{/snippet}

{#snippet folderIcon(linked)}
  <svg class="icon" viewBox="0 0 16 16" aria-hidden="true">
    <path
      d="M1.8 3.5h4l1.2 1.5h7.2v7.5H1.8z"
      fill="none"
      stroke="currentColor"
      stroke-width="1.2"
      stroke-linejoin="round"
      stroke-dasharray={linked ? "2 1.6" : "none"}
    />
  </svg>
{/snippet}

{#snippet fileRow(file)}
  <li>
    <ContextMenu label="{file.name} actions" items={fileMenu(file)}>
      {#if renaming?.kind === "file" && renaming.key === file.path}
        <!-- svelte-ignore a11y_autofocus -->
        <input
          class="row rename"
          autofocus
          bind:value={draft}
          aria-label="File name"
          spellcheck="false"
          onkeydown={(event) => {
            event.stopPropagation();
            if (event.key === "Enter" || event.key === "Tab") commitRename();
            if (event.key === "Escape") renaming = null;
          }}
          onblur={commitRename}
        />
      {:else}
        <button
          class="row file"
          class:current={file.open}
          class:picked={picked.has(file.path)}
          class:leaving={buffer?.mode === "cut" && buffer.paths.includes(file.path)}
          aria-current={file.open ? "page" : undefined}
          draggable="true"
          title={file.path}
          ondragstart={(event) => {
            // Dragging one of several picked files carries all of them: what
            // you can do to one, you can do to the ones you chose.
            const paths = scope(file);
            // Alt is copy, everywhere a file manager has ever existed.
            event.dataTransfer.setData("text/voxlogica-mode", event.altKey ? "copy" : "move");
            // A private type, so nothing else on the page mistakes this for
            // something it can handle.
            event.dataTransfer.setData("text/voxlogica-file", paths.join("\n"));
            event.dataTransfer.effectAllowed = "copyMove";
          }}
          ondblclick={() => startRename("file", file.path, file.name)}
          onkeydown={(event) => {
            if (event.key === "F2") {
              event.preventDefault();
              startRename("file", file.path, file.name);
            }
            if (event.key === "Delete" || event.key === "Backspace") {
              event.preventDefault();
              arm(scope(file));
            }
            if (event.metaKey || event.ctrlKey) {
              const key = event.key.toLowerCase();
              if (key === "x" || key === "c") {
                event.preventDefault();
                pickUp(scope(file), key === "x" ? "cut" : "copy");
              } else if (key === "v") {
                event.preventDefault();
                paste(file.project);
              }
            }
          }}
          onclick={(event) => choose(file, event)}
        >
          {@render fileIcon()}
          <span class="label">{file.name}</span>
        </button>
      {/if}
    </ContextMenu>
  </li>
{/snippet}

<nav class="library" aria-label="Library">
  <header>
    <input
      class="filter"
      type="search"
      placeholder="Filter"
      aria-label="Filter files by name"
      bind:value={filter}
      onkeydown={(event) => {
        event.stopPropagation();
        if (event.key === "Escape") filter = "";
      }}
    />
    <ContextMenu
      label="Sort files"
      items={ORDERS.map((entry) => ({
        label: entry.label,
        hint: order === entry.id ? "✓" : undefined,
        onselect: () => (order = entry.id),
      }))}
    >
      <Button
        tone="quiet"
        size="sm"
        title="Sorted by {ORDERS.find((entry) => entry.id === order)?.label} — right-click to change"
        onclick={() => (order = order === "name" ? "recent" : "name")}
      >
        ⇅
      </Button>
    </ContextMenu>
  </header>

  {#if armed}
    <!-- Two steps, inline. Not a modal: a dialogue that steals the keyboard to
         ask one question is worse than the mistake it prevents, and this can be
         ignored simply by carrying on. -->
    <div class="armed" role="alert">
      <span>
        {#if armed.kind === "project"}
          Delete the empty project “{armed.name}”?
        {:else}
          Delete {armed.paths.length === 1 ? "this file" : `${armed.paths.length} files`}?
        {/if}
      </span>
      <div class="pair">
        <Button tone="danger" size="sm" onclick={confirmDelete}>Delete</Button>
        <Button tone="quiet" size="sm" onclick={() => (armed = null)}>Cancel</Button>
      </div>
    </div>
  {:else if buffer}
    <p class="counted">
      {buffer.paths.length}
      {buffer.mode === "cut" ? "cut" : "copied"} —
      <button class="link" onclick={() => paste()}>paste</button>
      ·
      <button class="link" onclick={() => (buffer = null)}>drop</button>
    </p>
  {:else if picked.size > 1}
    <p class="counted">{picked.size} picked</p>
  {:else if needle}
    <p class="counted">{matches} of {library.files.length}</p>
  {/if}

  <div class="scroll">
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <section
      class:target={over === ""}
      ondragover={(event) => {
        event.preventDefault();
        over = "";
      }}
      ondragleave={() => (over = over === "" ? null : over)}
      ondrop={(event) => onDrop(event, null)}
    >
      <div class="head">
        <span class="kicker">Files</span>
        {#if buffer}
          <!-- The top is a destination like any other, and it needs saying so:
               without this, somebody looking at a file inside a project has no
               way to paste anywhere else. -->
          <Button tone="quiet" size="sm" title="Paste at the top" onclick={() => paste(null)}>
            Paste
          </Button>
        {/if}
        <Button tone="quiet" size="sm" title="New file" onclick={() => onnewfile?.(null)}>+</Button>
      </div>
      {#if loose.length === 0}
        <p class="empty">{needle ? "No match here" : "Nothing loose"}</p>
      {:else}
        <ul role="list">
          {#each loose as file (file.path)}
            {@render fileRow(file)}
          {/each}
        </ul>
      {/if}
    </section>

    {#each shown as project (project.path)}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <section
        class:target={over === project.name}
        ondragover={(event) => {
          event.preventDefault();
          over = project.name;
        }}
        ondragleave={() => (over = over === project.name ? null : over)}
        ondrop={(event) => onDrop(event, project.name)}
      >
        <ContextMenu label="{project.name} actions" items={projectMenu(project)}>
          <div class="head">
            {#if renaming?.kind === "project" && renaming.key === project.name}
              <!-- svelte-ignore a11y_autofocus -->
              <input
                class="row rename"
                autofocus
                bind:value={draft}
                aria-label="Project name"
                spellcheck="false"
                onkeydown={(event) => {
                  event.stopPropagation();
                  if (event.key === "Enter" || event.key === "Tab") commitRename();
                  if (event.key === "Escape") renaming = null;
                }}
                onblur={commitRename}
              />
            {:else}
              <button
                class="row project"
                title={project.path}
                aria-expanded={!folded.has(project.name)}
                ondblclick={() => !project.linked && startRename("project", project.name, project.name)}
                onclick={() => fold(project.name)}
              >
                <span class="chevron" class:folded={folded.has(project.name)}>›</span>
                {@render folderIcon(project.linked)}
                <span class="label">{project.name}</span>
              </button>
            {/if}
            <Button
              tone="quiet"
              size="sm"
              title="New file in {project.name}"
              onclick={() => onnewfile?.(project.name)}
            >
              +
            </Button>
          </div>
        </ContextMenu>

        {#if !folded.has(project.name)}
          {@const files = within(project.name)}
          {#if files.length === 0}
            <p class="empty">Empty</p>
          {:else}
            <ul role="list">
              {#each files as file (file.path)}
                {@render fileRow(file)}
              {/each}
            </ul>
          {/if}
        {/if}
      </section>
    {/each}
  </div>

  <footer>
    <Button tone="quiet" size="sm" onclick={() => onnewproject?.()}>New project</Button>
    <Button tone="quiet" size="sm" title="Show a folder you already have" onclick={() => onaddfolder?.()}>
      Add folder…
    </Button>
  </footer>
</nav>

<style>
  .library {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    /* The width is the rail's: a panel does not decide how wide it is when the
     * person dragging its edge has an opinion. */
    flex: 1;
    min-width: 0;
    min-height: 0;
  }

  header {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  .filter {
    flex: 1;
    min-width: 0;
    padding: var(--space-1) var(--space-2);
    background: var(--color-surface);
    border: none;
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    color: var(--color-text);
  }

  .filter::placeholder {
    color: var(--color-text-subtle);
  }

  .counted {
    padding: 0 var(--space-2);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .armed {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-2);
    border-radius: var(--radius-md);
    background: var(--color-danger-subtle);
    font-size: var(--text-2xs);
    color: var(--color-text);
  }

  .pair {
    display: flex;
    gap: var(--space-2);
  }

  .scroll {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    /* Room for a focus ring. A scroll container clips what leaves it, and an
     * outline drawn on the edge row was being sliced down its side. */
    overflow-x: hidden;
    padding: var(--ring-width) calc(var(--ring-width) + 1px);
  }

  section {
    border-radius: var(--radius-md);
    /* Shown only while something is over it: a permanent outline around every
     * drop target is a page wearing its mechanics on the outside. */
    outline: var(--border-width) dashed transparent;
    outline-offset: var(--space-1);
    transition: outline-color var(--motion-fast) var(--easing-standard);
  }

  section.target {
    outline-color: var(--color-accent);
  }

  .head {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  .kicker {
    flex: 1;
    padding: var(--space-1) var(--space-2);
    font-size: var(--text-2xs);
    font-weight: var(--weight-semibold);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-subtle);
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    text-align: left;
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  .label {
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Files sit under their project's name: the indent is the hierarchy, and the
   * icon says which of the two kinds of thing this row is. */
  li .row {
    padding-left: var(--space-5);
  }

  .icon {
    flex: none;
    width: 13px;
    height: 13px;
    color: var(--color-text-subtle);
  }

  .row:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .file.current {
    background: var(--color-accent-subtle);
    color: var(--color-text);
  }

  /* Picked, which is not the same as open: one is what you are looking at, the
   * other is what the next action applies to. Drawn inside the row, because a
   * ring outside it is a ring the scroll container cuts in half. */
  .file.picked {
    box-shadow: inset 0 0 0 var(--border-width) var(--color-accent);
  }

  /* On its way out, but still here: nothing is gone until it lands. */
  .file.leaving {
    opacity: 0.5;
  }

  .link {
    color: var(--color-accent);
    font-size: var(--text-2xs);
  }

  .link:hover {
    text-decoration: underline;
  }

  .row:focus-visible {
    outline: none;
    box-shadow: inset 0 0 0 var(--ring-width) var(--color-focus);
  }

  .file.current .icon {
    color: var(--color-accent);
  }

  .project {
    flex: 1;
    font-weight: var(--weight-semibold);
    color: var(--color-text-subtle);
  }

  .chevron {
    flex: none;
    width: 0.6rem;
    transform: rotate(90deg);
    transition: transform var(--motion-fast) var(--easing-standard);
  }

  .chevron.folded {
    transform: rotate(0deg);
  }

  .empty {
    padding: var(--space-1) var(--space-5);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .rename {
    flex: 1;
    background: var(--color-surface);
    border: none;
    outline: none;
    color: var(--color-text);
  }

  footer {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
    padding-top: var(--space-2);
  }
</style>
