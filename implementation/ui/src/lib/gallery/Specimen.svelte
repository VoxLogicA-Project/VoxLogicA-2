<script>
  /**
   * Renders one library entry: the real component, once per supported state.
   *
   * Nothing here is a picture or a copy. `entry.component` is the same module
   * the app imports, mounted with the props from `entry.variants`, so a
   * specimen that looks wrong *is* a component that is wrong.
   */
  let { entry } = $props();

  const Component = $derived(entry.component);
</script>

<article class="specimen">
  <header>
    <h3>{entry.name}</h3>
    <p class="summary">{entry.summary}</p>
    {#if entry.axes?.length}
      <p class="axes">
        {#each entry.axes as axis, index (axis)}<code>{axis}</code
          >{#if index < entry.axes.length - 1}<span class="sep">·</span>{/if}{/each}
      </p>
    {/if}
  </header>

  <div class="variants" class:stack={entry.layout === "stack"}>
    {#each entry.variants as variant (variant.label)}
      <div class="variant">
        <span class="variant-label">{variant.label}</span>
        <div class="stage">
          <Component {...variant.props}>
            {#if variant.stage === "region"}
              <!-- The dashed box is rendered *inside* the component, not around
                   it: a component that wraps a region (ContextMenu) must receive
                   the whole box as its child, or the gesture would only work on
                   the label and not in the space beside it. -->
              <div class="region">{variant.text ?? ""}</div>
            {:else}
              {variant.text ?? ""}
            {/if}
          </Component>
        </div>
      </div>
    {/each}
  </div>
</article>

<style>
  .specimen {
    padding-block: var(--space-5);
    border-top: var(--border-width) solid var(--color-border);
  }

  .specimen:first-child {
    border-top: none;
    padding-top: 0;
  }

  header {
    margin-bottom: var(--space-4);
  }

  .summary {
    margin-top: var(--space-1);
    color: var(--color-text-muted);
  }

  .axes {
    margin-top: var(--space-2);
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .axes code {
    font-size: inherit;
  }

  .sep {
    margin-inline: var(--space-1);
  }

  .variants {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: var(--space-4);
  }

  /* Some components are full-width rows (Toggle, Card) and read as broken in a
   * multi-column grid, so the entry can ask for a single column. */
  .variants.stack {
    grid-template-columns: minmax(0, 420px);
  }

  .variant {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    min-width: 0;
  }

  .variant-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-subtle);
  }

  .stage {
    display: flex;
    align-items: flex-start;
    min-width: 0;
  }

  .region {
    flex: 1;
    padding: var(--space-4);
    border: var(--border-width) dashed var(--color-border-strong);
    border-radius: var(--radius-md);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
  }
</style>
