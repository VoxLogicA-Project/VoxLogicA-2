<script>
  /**
   * The development design panel: moodboard, palette, typography, library.
   *
   * Read-only, deliberately. It is a mirror held up to the design system, not a
   * theme editor -- if a colour is wrong you fix `tokens.css`, and this panel
   * shows the result on the next reload. A panel that could override tokens at
   * runtime would let the app look right while the source stayed wrong.
   *
   * Never present in a production bundle: `main.js` imports this module only
   * behind `if (__DEV__)`, which esbuild resolves to `false` and drops.
   */
  import Components from "./sections/Components.svelte";
  import Moodboard from "./sections/Moodboard.svelte";
  import Palette from "./sections/Palette.svelte";
  import Typography from "./sections/Typography.svelte";

  const SECTIONS = [
    { id: "moodboard", title: "Moodboard", component: Moodboard },
    { id: "palette", title: "Palette", component: Palette },
    { id: "typography", title: "Typography", component: Typography },
    { id: "components", title: "Components", component: Components },
  ];

  const STORAGE_KEY = "voxlogica.dev-panel.section";

  /** Each section is a place, so each section has a URL.
   *
   * `#design`, `#design/palette`, … A panel reachable only by a keystroke is a
   * panel you cannot send to somebody, cannot bookmark, and cannot find again
   * after a reload — which for the one surface that documents the design system
   * is the difference between a tool and a secret. The hash is also what the CLI
   * prints when it starts a dev UI.
   */
  const ROUTE = /^#\/?design(?:\/([a-z-]+))?$/;

  let open = $state(false);
  let active = $state(SECTIONS[0].id);

  const Section = $derived(
    (SECTIONS.find((section) => section.id === active) ?? SECTIONS[0]).component,
  );

  function read() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }

  function remember(id) {
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* private mode; the panel just forgets */
    }
  }

  /** The URL is the source of truth for "is the panel open, and where". */
  function applyRoute() {
    const match = ROUTE.exec(location.hash);
    if (match === null) {
      open = false;
      return;
    }
    const wanted = SECTIONS.find((section) => section.id === match[1]);
    active = wanted?.id ?? read() ?? SECTIONS[0].id;
    open = true;
  }

  applyRoute();

  function href(id) {
    return `${location.pathname}${location.search}#design/${id}`;
  }

  function show() {
    // Pushed, not replaced: Back closes the panel, which is what Back means.
    location.hash = `#design/${active}`;
  }

  function select(id) {
    remember(id);
    // Replaced: walking the four sections should not fill the history with
    // steps the user has to press Back through to leave.
    history.replaceState(null, "", href(id));
    applyRoute();
  }

  function close() {
    // Drop our hash without adding an entry, so Back does not reopen the panel.
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    open = false;
  }

  function onKeydown(event) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
      return;
    }
    // Cmd/Ctrl+. — near Escape, unclaimed by browsers and by the app.
    if (event.key === "." && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (open) close();
      else show();
    }
  }
</script>

<svelte:window onkeydown={onKeydown} onhashchange={applyRoute} />

{#if !open}
  <!-- A real link, not a button: it goes to a URL, so it can be middle-clicked,
       copied, and read by anyone wondering how to get back here. -->
  <a class="trigger" href={href(active)} title="Design system (⌘.)">Design</a>
{:else}
  <div class="sheet" role="dialog" aria-modal="true" aria-label="Design system, read only">
    <nav aria-label="Design system sections">
      <div class="brand">
        <span class="kicker">dev</span>
        <h2>Design system</h2>
        <p class="readonly">Read-only. Edit <code>tokens.css</code> to change anything here.</p>
      </div>
      <ul role="list">
        {#each SECTIONS as section (section.id)}
          <li>
            <a
              class="nav-item"
              class:current={section.id === active}
              aria-current={section.id === active ? "page" : undefined}
              href={href(section.id)}
              onclick={(event) => {
                event.preventDefault();
                select(section.id);
              }}
            >
              {section.title}
            </a>
          </li>
        {/each}
      </ul>
      <button class="close" onclick={close}>Close <kbd>esc</kbd></button>
    </nav>

    <div class="content">
      <Section />
    </div>
  </div>
{/if}

<style>
  .trigger {
    position: fixed;
    /* Bottom-right, because that is where an out-of-band dev affordance lives
     * without ever sitting on top of the app's own content. */
    right: var(--space-4);
    bottom: var(--space-4);
    z-index: var(--layer-dev);
    padding: var(--space-1) var(--space-3);
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border-strong);
    border-radius: var(--radius-full);
    box-shadow: var(--shadow-md);
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  .trigger,
  .nav-item {
    text-decoration: none;
    display: block;
  }

  .trigger:hover {
    color: var(--color-text);
    background: var(--color-surface-hover);
  }

  .sheet {
    position: fixed;
    inset: 0;
    z-index: var(--layer-dev);
    display: grid;
    grid-template-columns: 232px minmax(0, 1fr);
    background: var(--color-canvas);
  }

  nav {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-5) var(--space-4);
    border-right: var(--border-width) solid var(--color-border);
    background: var(--color-surface);
    overflow-y: auto;
  }

  .kicker {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-accent);
  }

  .brand h2 {
    margin-top: var(--space-1);
    font-size: var(--text-md);
    text-transform: none;
    letter-spacing: var(--tracking-tight);
  }

  .readonly {
    margin-top: var(--space-2);
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  nav ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .nav-item {
    width: 100%;
    padding: var(--space-2) var(--space-2);
    border-radius: var(--radius-md);
    text-align: left;
    font-size: var(--text-base);
    color: var(--color-text-muted);
  }

  .nav-item:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .nav-item.current {
    background: var(--color-accent-subtle);
    color: var(--color-accent);
    font-weight: var(--weight-medium);
  }

  .close {
    margin-top: auto;
    align-self: flex-start;
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
  }

  .close:hover {
    color: var(--color-text);
  }

  kbd {
    padding: 0 var(--space-1);
    border: var(--border-width) solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    font-size: var(--text-2xs);
  }

  .content {
    padding: var(--space-6) var(--space-6) var(--space-8);
    overflow-y: auto;
  }
</style>
