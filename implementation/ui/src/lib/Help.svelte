<script>
  /**
   * The shortcut list, as a sheet over the board.
   *
   * Not a library component: it documents this application, and a design system
   * component that knows what `mod+D` does in VoxLogicA would be a component
   * only one application could ever use. It is built out of the library, like
   * anything else here.
   *
   * The list itself lives next to the code that implements it (`Bento`'s
   * `SHORTCUTS`), because a list of shortcuts maintained separately from the
   * shortcuts is a list that is wrong by the second release.
   */
  import { Button } from "./components/index.js";

  let { shortcuts = [], onclose } = $props();

  /** `mod` is whichever key this platform actually uses. */
  const mod = typeof navigator !== "undefined" && /Mac|iP/.test(navigator.platform) ? "⌘" : "Ctrl";

  const rows = $derived(
    shortcuts.map((row) => ({ ...row, keys: row.keys.replaceAll("mod+", `${mod}`) })),
  );

  let sheet = $state(null);

  $effect(() => {
    sheet?.focus();
  });
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
  class="scrim"
  role="presentation"
  onclick={onclose}
  onkeydown={(event) => event.key === "Escape" && onclose?.()}
>
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    bind:this={sheet}
    class="sheet"
    role="dialog"
    aria-modal="true"
    aria-label="Keyboard shortcuts"
    tabindex="-1"
    onclick={(event) => event.stopPropagation()}
    onkeydown={(event) => event.key === "Escape" && onclose?.()}
  >
    <header>
      <h2>Shortcuts</h2>
      <Button tone="quiet" size="sm" onclick={onclose}>Close <kbd>esc</kbd></Button>
    </header>

    <dl>
      {#each rows as row (row.keys)}
        <div class="row">
          <dt><kbd>{row.keys}</kbd></dt>
          <dd>{row.does}</dd>
        </div>
      {/each}
    </dl>
  </div>
</div>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: var(--layer-overlay);
    display: grid;
    place-items: center;
    padding: var(--space-5);
    /* Dimmed, not hidden: the board is still the thing, this is a note about it. */
    background: var(--color-overlay);
  }

  .sheet {
    width: min(34rem, 100%);
    max-height: 100%;
    overflow: auto;
    padding: var(--space-5);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-overlay);
  }

  .sheet:focus {
    outline: none;
  }

  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-4);
    margin-bottom: var(--space-4);
  }

  h2 {
    font-size: var(--text-md);
  }

  dl {
    margin: 0;
  }

  .row {
    display: grid;
    /* The keys column is sized to the longest chord rather than to a guess, so
     * the descriptions line up without anything being truncated. */
    grid-template-columns: max-content minmax(0, 1fr);
    gap: var(--space-2) var(--space-4);
    padding: var(--space-1) 0;
  }

  dt {
    color: var(--color-text-muted);
  }

  dd {
    margin: 0;
    color: var(--color-text);
  }

  kbd {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    white-space: nowrap;
  }
</style>
