<script>
  /**
   * A bounded region of related content.
   *
   * The only container in the system, so that "grouped" always looks the same.
   * Elevation is carried by a border and a surface change rather than a shadow:
   * on a dense screen, stacked shadows turn into haze. `flush` drops the body
   * padding for content that draws its own edges, such as a list or a log.
   */
  let {
    title = undefined,
    subtitle = undefined,
    flush = false,
    actions = undefined,
    children,
  } = $props();
</script>

<section class="card">
  {#if title || actions}
    <header>
      <div class="titles">
        {#if title}<h2>{title}</h2>{/if}
        {#if subtitle}<p class="subtitle">{subtitle}</p>{/if}
      </div>
      {#if actions}<div class="actions">{@render actions()}</div>{/if}
    </header>
  {/if}
  <div class="body" class:flush>
    {@render children?.()}
  </div>
</section>

<style>
  .card {
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
  }

  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-4);
    border-bottom: var(--border-width) solid var(--color-border);
  }

  .titles {
    min-width: 0;
  }

  h2 {
    /* A card title is a label, not a headline: it earns its hierarchy from
     * weight and colour, not size, so a screen of cards stays flat and calm. */
    font-size: var(--text-xs);
    font-weight: var(--weight-semibold);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-muted);
  }

  .subtitle {
    margin-top: var(--space-1);
    font-size: var(--text-sm);
    color: var(--color-text-muted);
  }

  .actions {
    flex: none;
    display: flex;
    gap: var(--space-2);
  }

  .body {
    padding: var(--space-4);
  }

  .body.flush {
    padding: 0;
  }
</style>
