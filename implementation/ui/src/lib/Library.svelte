<script>
  /**
   * The sidebar: every file there is, and which one is open.
   *
   * This *is* the tab bar. One file shows in the pane at a time and the list
   * beside it is how you get to another, so there is no strip of tabs across
   * the top -- a tab strip is a second copy of this list, in a different order,
   * truncated, and it is where "which of these nine is the one I mean" comes
   * from. Here the order is the library's own and nothing is ever hidden behind
   * a chevron.
   *
   * Projects are folders and files are files, which is why drag-and-drop can be
   * literal: drop a file on a project and the file moves into that folder. Put
   * the folder in a repository and git has ordinary files to track.
   */
  import { Button, ContextMenu } from "./components/index.js";

  let {
    library = { root: "", projects: [], files: [] },
    onopen,
    onnewfile,
    onnewproject,
    onmove,
    onrenamefile,
    onrenameproject,
    ondelete,
  } = $props();

  /** Loose files first: "unfiled" is a place, not a limbo. */
  const loose = $derived(library.files.filter((file) => file.project === null));
  const inProject = $derived((name) => library.files.filter((file) => file.project === name));

  /** The project a dragged file is currently over, or "" for the top level. */
  let over = $state(null);
  /** What is being renamed: `{kind: "file"|"project", key}`. */
  let renaming = $state(null);
  let draft = $state("");

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

  function menuFor(file) {
    return [
      { label: "Rename", hint: "F2", onselect: () => startRename("file", file.path, file.name) },
      ...(file.project === null
        ? []
        : [{ label: "Move to the top", onselect: () => onmove?.(file.path, null) }]),
      ...library.projects
        .filter((name) => name !== file.project)
        .map((name) => ({ label: `Move to ${name}`, onselect: () => onmove?.(file.path, name) })),
      { separator: true },
      { label: "Delete", danger: true, onselect: () => ondelete?.(file.path) },
    ];
  }
</script>

{#snippet fileRow(file)}
  <li>
    <ContextMenu label="{file.name} actions" items={menuFor(file)}>
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
          ondragstart={(event) => {
            // A private type, so nothing else on the page mistakes this for a
            // file it can handle.
            event.dataTransfer.setData("text/voxlogica-file", file.path);
            event.dataTransfer.effectAllowed = "move";
          }}
          ondblclick={() => startRename("file", file.path, file.name)}
          onclick={() => onopen?.(file.path)}
        >
          {file.name}
        </button>
      {/if}
    </ContextMenu>
  </li>
{/snippet}

<nav class="library" aria-label="Library">
  <header>
    <span class="kicker">Files</span>
    <Button tone="quiet" size="sm" title="New file" onclick={() => onnewfile?.(null)}>+</Button>
  </header>

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
    <ul role="list">
      {#each loose as file (file.path)}
        {@render fileRow(file)}
      {/each}
    </ul>
  </section>

  {#each library.projects as project (project)}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <section
      class:target={over === project}
      ondragover={(event) => {
        event.preventDefault();
        over = project;
      }}
      ondragleave={() => (over = over === project ? null : over)}
      ondrop={(event) => onDrop(event, project)}
    >
      <div class="project">
        {#if renaming?.kind === "project" && renaming.key === project}
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
            class="row name"
            ondblclick={() => startRename("project", project, project)}
            title="Double-click to rename"
          >
            {project}
          </button>
        {/if}
        <Button
          tone="quiet"
          size="sm"
          title="New file in {project}"
          onclick={() => onnewfile?.(project)}
        >
          +
        </Button>
      </div>
      <ul role="list">
        {#each inProject(project) as file (file.path)}
          {@render fileRow(file)}
        {/each}
      </ul>
    </section>
  {/each}

  <footer>
    <Button tone="quiet" size="sm" onclick={() => onnewproject?.()}>New project</Button>
  </footer>
</nav>

<style>
  .library {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    width: 13rem;
    flex: none;
    min-height: 0;
    overflow: auto;
    padding-right: var(--space-2);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .kicker {
    font-size: var(--text-2xs);
    font-weight: var(--weight-semibold);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-subtle);
  }

  section {
    border-radius: var(--radius-md);
    /* Only while something is being dragged over it: a permanent outline around
     * every drop target is a page wearing its mechanics on the outside. */
    outline: var(--border-width) dashed transparent;
    outline-offset: var(--space-1);
    transition: outline-color var(--motion-fast) var(--easing-standard);
  }

  section.target {
    outline-color: var(--color-accent);
  }

  .project {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .row {
    display: block;
    width: 100%;
    text-align: left;
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    color: var(--color-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .row:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .file.current {
    background: var(--color-accent-subtle);
    color: var(--color-text);
  }

  .name {
    font-weight: var(--weight-semibold);
    color: var(--color-text-subtle);
  }

  .rename {
    background: var(--color-surface);
    border: none;
    outline: none;
    color: var(--color-text);
  }

  footer {
    margin-top: auto;
    padding-top: var(--space-2);
  }
</style>
