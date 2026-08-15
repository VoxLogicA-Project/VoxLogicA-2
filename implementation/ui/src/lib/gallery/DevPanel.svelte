<script>
  /**
   * The dev page: moodboard, palette, typography, the component library, and
   * the live state of this process.
   *
   * This is where everything that is *about* the software lives, so that the
   * application's own page can hold only what a user of VoxLogicA came for. The
   * four design sections are read-only by design -- a mirror held up to the
   * system, not a theme editor. If a colour is wrong you fix `tokens.css` and
   * the panel shows the result on the next reload; a panel that could override
   * tokens at runtime would let the app look right while the source stayed
   * wrong. Debug is the one live section, and reads the store.
   *
   * Never present in a production bundle: `main.js` imports this module only
   * behind `if (__DEV__)`, which esbuild resolves to `false` and drops.
   */
  import { Button } from "../components/index.js";
  import Components from "./sections/Components.svelte";
  import Debug from "./sections/Debug.svelte";
  import Moodboard from "./sections/Moodboard.svelte";
  import Palette from "./sections/Palette.svelte";
  import Typography from "./sections/Typography.svelte";

  // Design first, then the live instrument. The order is the reading order of
  // the system: what it should look like, then what it is currently doing.
  const SECTIONS = [
    { id: "moodboard", title: "Moodboard", component: Moodboard },
    { id: "palette", title: "Palette", component: Palette },
    { id: "typography", title: "Typography", component: Typography },
    { id: "components", title: "Components", component: Components },
    { id: "debug", title: "Debug", component: Debug, live: true },
  ];

  const STORAGE_KEY = "voxlogica.dev-panel.section";

  /** Each section is a place, so each section has a URL.
   *
   * `#dev`, `#dev/palette`, … A page reachable only by a keystroke is a page you
   * cannot send to somebody, cannot bookmark, and cannot find again after a
   * reload. The hash is also what the CLI prints when it starts a dev UI.
   */
  const ROUTE = /^#\/?dev(?:\/([a-z-]+))?$/;

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
    return `${location.pathname}${location.search}#dev/${id}`;
  }

  function show(id = active) {
    remember(id);
    // Pushed, not replaced: Back closes the panel, which is what Back means.
    location.hash = `#dev/${id}`;
  }

  function select(id) {
    remember(id);
    // Replaced: walking the sections should not fill the history with
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
  <!-- One button, no menu: the dev page has a nav of its own, and a menu in
       front of it would be a second copy of that nav to keep in step. It is a
       library Button -- the dev affordance is not allowed a private widget. -->
  <div class="dock">
    <Button size="sm" title="Dev page (⌘.)" onclick={() => show()}>dev</Button>
  </div>
{:else}
  <div class="sheet" role="dialog" aria-modal="true" aria-label="Dev page">
    <nav aria-label="Dev sections">
      <div class="brand">
        <span class="kicker">dev</span>
        <h2>Dev</h2>
        <p class="readonly">
          The design system is read-only — edit <code>tokens.css</code>. Debug is live.
        </p>
      </div>
      <ul role="list">
        {#each SECTIONS as section (section.id)}
          <li>
            <a
              class="nav-item"
              class:current={section.id === active}
              class:live={section.live}
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
  /* The dock only positions; the control inside it is a plain library Button,
   * which is the point -- a bespoke pill here would be the first component
   * outside the system. Bottom-right: out of the way of the app's own content. */
  .dock {
    position: fixed;
    right: var(--space-4);
    bottom: var(--space-4);
    z-index: var(--layer-dev);
    box-shadow: var(--shadow-md);
    border-radius: var(--radius-md);
  }

  .nav-item {
    text-decoration: none;
    display: block;
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

  /* The live section is set apart from the four documentation ones by a rule,
   * not by a colour: it is a different kind of page, not a more important one. */
  .nav-item.live {
    margin-top: var(--space-2);
    padding-top: var(--space-3);
    border-top: var(--border-width) solid var(--color-border);
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
