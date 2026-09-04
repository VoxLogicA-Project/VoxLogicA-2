<script>
  /** The type system, read back out of the live stylesheet. */
  import { typeGroups } from "../tokens.js";

  const type = typeGroups();
  const SAMPLE = "Percentiles of the FLAIR volume";
</script>

<p class="lede">
  A ~1.2 modular scale from a 14px UI base, in the platform's own sans. No web
  fonts: the bundle is served by the run's own process and must stay
  self-contained, and a system stack renders at native hinting everywhere.
</p>

<section>
  <h3>Scale</h3>
  <ul class="scale" role="list">
    {#each type.sizes as token (token.name)}
      <li>
        <code class="name">{token.name}</code>
        <code class="value">{token.value}</code>
        <span class="sample" style="font-size: var({token.name});">{SAMPLE}</span>
      </li>
    {/each}
  </ul>
</section>

<section>
  <h3>Headings</h3>
  <div class="stack">
    <h1>Whole-tumour segmentation</h1>
    <h2>Distance-bounded growth</h2>
    <h3>Threshold sweep</h3>
    <h4>Per-case Dice</h4>
    <p>
      Body text sits at 14px with a 1.55 line height and a 68ch measure, which
      is where a paragraph stops needing effort to read. Headings balance their
      line breaks and carry negative tracking, because at these sizes the
      default spacing looks loose.
    </p>
  </div>
</section>

<section>
  <h3>Weight, tracking, leading</h3>
  <ul class="rows" role="list">
    {#each type.weights as token (token.name)}
      <li>
        <code class="name">{token.name}</code>
        <code class="value">{token.value}</code>
        <span style="font-weight: var({token.name});">{SAMPLE}</span>
      </li>
    {/each}
    {#each type.tracking as token (token.name)}
      <li>
        <code class="name">{token.name}</code>
        <code class="value">{token.value}</code>
        <span style="letter-spacing: var({token.name});">{SAMPLE}</span>
      </li>
    {/each}
    {#each type.leading as token (token.name)}
      <li>
        <code class="name">{token.name}</code>
        <code class="value">{token.value}</code>
        <span class="wrapped" style="line-height: var({token.name});"
          >{SAMPLE} {SAMPLE}</span
        >
      </li>
    {/each}
  </ul>
</section>

<section>
  <h3>Numerals</h3>
  <p class="note">
    Values that change in place are tabular, so a counter cannot reflow the row
    it lives in. Compare:
  </p>
  <div class="numerals">
    <div>
      <span class="numeral-label">proportional</span>
      <div class="proportional">0.8676 · 1.1119 · 369 cases</div>
      <div class="proportional">0.8710 · 0.9000 · 111 cases</div>
    </div>
    <div>
      <span class="numeral-label">tabular (.numeric)</span>
      <div class="numeric">0.8676 · 1.1119 · 369 cases</div>
      <div class="numeric">0.8710 · 0.9000 · 111 cases</div>
    </div>
  </div>
</section>

<section>
  <h3>Families</h3>
  <ul class="rows" role="list">
    {#each type.families as token (token.name)}
      <li>
        <code class="name">{token.name}</code>
        <span style="font-family: var({token.name});">{SAMPLE} 0123456789</span>
      </li>
    {/each}
  </ul>
</section>

<style>
  .lede {
    margin-bottom: var(--space-6);
    color: var(--color-text-muted);
  }

  section {
    margin-bottom: var(--space-6);
  }

  h3 {
    margin-bottom: var(--space-3);
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

  .scale li,
  .rows li {
    display: grid;
    grid-template-columns: 9rem 5rem 1fr;
    align-items: baseline;
    gap: var(--space-3);
    padding-block: var(--space-2);
    border-top: var(--border-width) solid var(--color-border);
  }

  .rows li:first-child,
  .scale li:first-child {
    border-top: none;
  }

  .name {
    font-size: var(--text-xs);
  }

  .value {
    font-size: var(--text-2xs);
    color: var(--color-text-subtle);
  }

  .sample {
    line-height: var(--leading-tight);
    letter-spacing: var(--tracking-tight);
  }

  .wrapped {
    max-width: 22ch;
    display: block;
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .numerals {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: var(--space-5);
  }

  .numeral-label {
    display: block;
    margin-bottom: var(--space-2);
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-subtle);
  }

  .proportional {
    font-variant-numeric: proportional-nums;
  }
</style>
