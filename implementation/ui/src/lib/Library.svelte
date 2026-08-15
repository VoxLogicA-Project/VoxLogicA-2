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
    const path = event.dataTransfer?.getData("text/voxlogica-file");
    if (path) onmove?.(path, project);
  }

  function fileMenu(file) {
    return [
      { label: "Rename", hint: "F2", onselect: () => startRename("file", file.path, file.name) },
      { label: "Show in folder", onselect: () => onreveal?.(file.path) },
      { separator: true },
      ...(file.project === null
        ? []
        : [{ label: "Move to the top", onselect: () => onmove?.(file.path, null) }]),
      ...library.projects
        .filter((project) => project.name !== file.project)
        .map((project) => ({
          label: `Move to ${project.name}`,
          onselect: () => onmove?.(file.path, project.name),
        })),
      { separator: true },
      { label: "Delete", danger: true, onselect: () => ondelete?.(file.path) },
    ];
  }

  function projectMenu(project) {
    return [
      {
        label: "Rename",
        disabled: project.linked,
        hint: project.linked ? "linked folders keep their own name" : "double-click",
        onselect: () => startRename("project", project.name, project.name),
      },
      { label: "New file here", onselect: () => onnewfile?.(project.name) },
      { label: "Show in folder", onselect: () => onreveal?.(project.path) },
      ...(project.linked
        ? [
            { separator: true },
            {
              label: "Remove from the list",
              hint: "the folder itself is untouched",
              onselect: () => onforgetfolder?.(project.path),
            },
          ]
        : []),
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
          aria-current={file.open ? "page" : undefined}
          draggable="true"
          title={file.path}
          ondragstart={(event) => {
            // A private type, so nothing else on the page mistakes this for
            // something it can handle.
            event.dataTransfer.setData("text/voxlogica-file", file.path);
            event.dataTransfer.effectAllowed = "move";
          }}
          ondblclick={() => startRename("file", file.path, file.name)}
          onkeydown={(event) => {
            if (event.key === "F2") {
              event.preventDefault();
              startRename("file", file.path, file.name);
            }
          }}
          onclick={() => onopen?.(file.path)}
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

  {#if needle}
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
    width: 15rem;
    flex: none;
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

  .scroll {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    flex: 1;
    min-height: 0;
    overflow: auto;
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
