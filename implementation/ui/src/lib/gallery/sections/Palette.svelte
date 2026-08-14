<script>
  /** The palette, read back out of the live stylesheet. Read-only by design. */
  import { tokenGroups } from "../tokens.js";

  const groups = tokenGroups();
</script>

<p class="lede">
  Read from the running stylesheet, so this page cannot disagree with
  <code>tokens.css</code>. Two tiers: primitives are the ramp, roles are what a
  component is allowed to name.
</p>

{#each groups as group (group.id)}
  <section>
    <h3>{group.title}</h3>
    {#if group.note}<p class="note">{group.note}</p>{/if}

    {#if group.kind === "color"}
      <ul class="swatches" role="list">
        {#each group.tokens as token (token.name)}
          <li>
            <span class="swatch" style="background: var({token.name});"></span>
            <code class="name">{token.name}</code>
            <code class="value">{token.value}</code>
          </li>
        {/each}
      </ul>
    {:else if group.kind === "space"}
      <ul class="bars" role="list">
        {#each group.tokens as token (token.name)}
          <li>
            <code class="name">{token.name}</code>
            <span class="bar" style="width: var({token.name});"></span>
            <code class="value">{token.value}</code>
          </li>
        {/each}
      </ul>
    {:else if group.kind === "radius"}
      <ul class="radii" role="list">
        {#each group.tokens as token (token.name)}
          <li>
            <span class="radius" style="border-radius: var({token.name});"></span>
            <code class="name">{token.name}</code>
          </li>
        {/each}
      </ul>
    {:else if group.kind === "shadow"}
      <ul class="shadows" role="list">
        {#each group.tokens as token (token.name)}
          <li>
            <span class="elevated" style="box-shadow: var({token.name});"></span>
            <code class="name">{token.name}</code>
          </li>
        {/each}
      </ul>
    {:else}
      <ul class="raw" role="list">
        {#each group.tokens as token (token.name)}
          <li><code class="name">{token.name}</code><code class="value">{token.value}</code></li>
        {/each}
      </ul>
    {/if}
  </section>
{/each}

<style>
  .lede {
    margin-bottom: var(--space-6);
    color: var(--color-text-muted);
  }

  section {
    margin-bottom: var(--space-6);
  }

  h3 {
    margin-bottom: var(--space-1);
  }

  .note {
    margin-bottom: var(--space-3);
    font-size: var(--text-sm);
    color: var(--color-text-muted);
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .swatches {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: var(--space-3);
  }

  .swatches li {
    display: grid;
    grid-template-columns: auto 1fr;
    grid-template-rows: auto auto;
    align-items: center;
    gap: 0 var(--space-2);
  }

  .swatch {
    grid-row: 1 / 3;
    width: 32px;
    height: 32px;
    border: var(--border-width) solid var(--color-border-strong);
    border-radius: var(--radius-sm);
  }

  .name {
    font-size: var(--text-xs);
    color: var(--color-text);
  }

  .value {
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
    overflow-wrap: anywhere;
  }

  .bars li {
    display: grid;
    grid-template-columns: 7rem 1fr auto;
    align-items: center;
    gap: var(--space-3);
    padding-block: var(--space-1);
  }

  .bar {
    height: 10px;
    background: var(--color-accent);
    border-radius: var(--radius-sm);
  }

  .radii,
  .shadows {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-5);
  }

  .radii li,
  .shadows li {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .radius {
    width: 56px;
    height: 40px;
    background: var(--color-surface-sunken);
    border: var(--border-width) solid var(--color-border-strong);
  }

  .elevated {
    width: 72px;
    height: 48px;
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    border-radius: var(--radius-md);
  }

  .raw li {
    display: grid;
    grid-template-columns: 10rem 1fr;
    gap: var(--space-3);
    padding-block: var(--space-1);
  }
</style>
