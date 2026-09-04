<script>
  /**
   * What the interface is trying to be, shown with the real components.
   *
   * Not a mood *board* of borrowed images -- there is nothing here that the app
   * does not itself render. Four principles, and the evidence for each.
   */
  import { Button, Card, ContextMenu, Toggle } from "../../components/index.js";

  let follow = $state(true);

  const PRINCIPLES = [
    {
      title: "Powerful simplicity",
      body: "Few parts, each doing one thing completely. Four components carry the whole interface; a fifth has to earn its place by removing something.",
    },
    {
      title: "Clarity over decoration",
      body: "One accent colour, one focus ring, one container. Depth is a border and a surface change. Nothing is styled to look important — importance is shown by position and weight.",
    },
    {
      title: "Dense, not cramped",
      body: "A 4px rhythm and a 14px base let a lot of state fit on one screen while every group still has air around it.",
    },
    {
      title: "Calm under load",
      body: "A run can push events for hours. Numbers are tabular so they cannot reflow their neighbours, motion is short and never bounces, and nothing moves that the user did not ask to move.",
    },
  ];
</script>

<p class="lede">
  Minimalist, user-friendly, powerful, clear — with the components that make
  those words concrete.
</p>

<section class="principles">
  {#each PRINCIPLES as principle (principle.title)}
    <div class="principle">
      <h3>{principle.title}</h3>
      <p>{principle.body}</p>
    </div>
  {/each}
</section>

<section>
  <h3>In practice</h3>
  <p class="note">
    A composed view, built only from library components and semantic tokens.
    Right-click the run row.
  </p>

  <div class="demo">
    <Card title="Run" subtitle="brats021.imgql">
      {#snippet actions()}
        <Button tone="quiet" size="sm">Log</Button>
        <Button tone="accent" size="sm">Open</Button>
      {/snippet}

      <ContextMenu
        label="Run actions"
        items={[
          { label: "Copy program path", hint: "⌘C" },
          { label: "Reveal task graph" },
          { separator: true },
          { label: "Evict from cache", danger: true },
        ]}
      >
        <dl class="facts">
          <dt>status</dt>
          <dd>completed</dd>
          <dt>elapsed</dt>
          <dd class="numeric">148.32s</dd>
          <dt>nodes</dt>
          <dd class="numeric">18 402</dd>
        </dl>
      </ContextMenu>

      <div class="row">
        <Toggle
          bind:checked={follow}
          label="Follow the log"
          description="Scroll to the newest event as it arrives."
        />
      </div>
    </Card>
  </div>
</section>

<section>
  <h3>Surfaces</h3>
  <p class="note">
    Three levels, and that is all there is: sunken for inset content, surface
    for a card, overlay for something genuinely detached.
  </p>
  <div class="surfaces">
    <div class="sunken">sunken</div>
    <div class="surface">surface</div>
    <div class="overlay">overlay</div>
  </div>
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
    margin-bottom: var(--space-2);
  }

  .note {
    margin-bottom: var(--space-4);
    font-size: var(--text-sm);
    color: var(--color-text-muted);
  }

  .principles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: var(--space-5);
  }

  .principle p {
    margin-top: var(--space-1);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
  }

  .demo {
    max-width: 420px;
  }

  .facts {
    display: grid;
    grid-template-columns: 5rem 1fr;
    gap: var(--space-1) var(--space-4);
    margin: 0;
  }

  .facts dt {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
  }

  .facts dd {
    margin: 0;
  }

  .row {
    margin-top: var(--space-4);
    padding-top: var(--space-4);
    border-top: var(--border-width) solid var(--color-border);
  }

  .surfaces {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-4);
  }

  .surfaces > * {
    display: grid;
    place-items: center;
    width: 128px;
    height: 72px;
    border-radius: var(--radius-md);
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  .sunken {
    background: var(--color-surface-sunken);
    border: var(--border-width) solid var(--color-border);
  }

  .surface {
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    box-shadow: var(--shadow-sm);
  }

  .overlay {
    background: var(--color-surface);
    border: var(--border-width) solid var(--color-border);
    box-shadow: var(--shadow-overlay);
  }
</style>
