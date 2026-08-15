<script>
  /**
   * What this process is doing right now: instance, run, event log.
   *
   * A dev instrument, which is why it lives here and not in the app. The app's
   * page is deliberately empty until it has something of its own to show, and
   * "the state of the machinery" is not that -- it is what you open when you are
   * debugging the machinery, alongside the palette and the component library.
   *
   * The only section that reads the store. The four design sections are
   * documentation and would render identically in a dead process; this one is
   * meaningless without a live connection, and says so when there is none.
   */
  import { Button, Card, ContextMenu, Toggle } from "../../components/index.js";
  import { app } from "../../state.svelte.js";

  /** Frozen copy of the log, or `null` while it is following. */
  let held = $state(null);

  const events = $derived(held ?? app.recentEvents);

  const statusLabel = $derived(
    app.connection.status === "connected"
      ? `connected · ${app.connection.clientId ?? "?"}`
      : app.connection.status,
  );

  const instanceFields = $derived(
    app.instance
      ? [
          ["pid", String(app.instance.pid)],
          ["port", String(app.instance.port)],
          ["store", app.instance.storeDb ?? "default"],
          ["mode", app.instance.dev ? "dev · live reload" : "shipped bundle"],
          ["clients", String(app.instance.clients)],
        ]
      : [],
  );

  const runFields = $derived([
    ["program", app.run.program ?? "—"],
    ["status", app.run.status + (app.running ? " …" : "")],
    ...(app.nodeCount === null ? [] : [["nodes", String(app.nodeCount)]]),
    ...(app.run.elapsed == null ? [] : [["elapsed", `${app.run.elapsed.toFixed(2)}s`]]),
  ]);

  /** Freezing takes a snapshot; following drops it and the live list shows again. */
  function hold(paused) {
    held = paused ? app.recentEvents : null;
  }

  function copy(text) {
    // No feedback beyond the platform's own: a toast for a copy is noise, and
    // the clipboard is verifiable by pasting.
    navigator.clipboard?.writeText(text);
  }

  function diagnostics() {
    return JSON.stringify(
      { instance: app.instance, run: app.run, connection: app.connection },
      null,
      2,
    );
  }
</script>

{#snippet fields(rows)}
  <dl>
    {#each rows as [key, value] (key)}
      <dt>{key}</dt>
      <dd class="numeric">{value}</dd>
    {/each}
  </dl>
{/snippet}

<div class="lede">
  <p>
    Live state of this process. The only page here that is not documentation —
    everything below comes from the store, over the same WebSocket that keeps the
    run alive while you are looking at it.
  </p>
  <span class="status {app.connection.status}">{statusLabel}</span>
  <Button tone="quiet" size="sm" onclick={() => copy(diagnostics())}>
    Copy diagnostics
  </Button>
</div>

<div class="stack">
  <Card title="Instance">
    {#if app.instance}
      {@render fields(instanceFields)}
    {:else}
      <p class="empty">No instance information yet.</p>
    {/if}
  </Card>

  <Card title="Run" subtitle={app.run.program ?? undefined}>
    {@render fields(runFields)}
  </Card>

  <Card title="Events" flush>
    {#snippet actions()}
      <!-- The wrapper cancels Toggle's negative margin, which exists so the
           row's hit area bleeds past its text; inside a card header that bleed
           would cross the border. -->
      <div class="hold"><Toggle label="Hold" checked={held !== null} onchange={hold} /></div>
    {/snippet}

    {#if events.length === 0}
      <p class="empty padded">Nothing yet.</p>
    {:else}
      <!-- A list of divs rather than ul/li: ContextMenu wraps each row in an
           element of its own, and however invisible that element is
           (display:contents), a <div> between <ul> and <li> is invalid. -->
      <div class="events" role="list">
        {#each events as event, index (index)}
          <ContextMenu
            label="Event actions"
            items={[
              { label: "Copy event", hint: "JSON", onselect: () => copy(JSON.stringify(event)) },
              { label: "Copy message", disabled: !event.message,
                onselect: () => copy(event.message ?? "") },
            ]}
          >
            <div class="event" role="listitem">
              <code>{event.type}</code>
              <span class="message">{event.message ?? ""}</span>
            </div>
          </ContextMenu>
        {/each}
      </div>
    {/if}
  </Card>
</div>

<style>
  .lede {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-bottom: var(--space-6);
  }

  .lede p {
    color: var(--color-text-muted);
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    /* A measure, not full bleed: this is read top to bottom, and a 2000px-wide
     * key/value list is unreadable. */
    max-width: 52rem;
  }

  .status {
    flex: none;
    margin-left: auto;
    padding: 0 var(--space-2);
    border: var(--border-width) solid var(--color-border-strong);
    border-radius: var(--radius-full);
    font-size: var(--text-2xs);
    color: var(--color-text-muted);
  }

  .status.connected {
    color: var(--color-ok);
    border-color: var(--color-ok);
  }

  .status.disconnected {
    color: var(--color-danger);
    border-color: var(--color-danger);
  }

  dl {
    display: grid;
    grid-template-columns: 7rem minmax(0, 1fr);
    gap: var(--space-1) var(--space-4);
    margin: 0;
  }

  dt {
    color: var(--color-text-muted);
  }

  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }

  .empty {
    color: var(--color-text-subtle);
  }

  .padded {
    padding: var(--space-4);
  }

  .hold {
    padding: var(--space-2);
    min-width: 8rem;
  }

  .events {
    max-height: 22rem;
    overflow-y: auto;
  }

  .event {
    display: flex;
    gap: var(--space-3);
    padding: var(--space-1) var(--space-4);
    border-top: var(--border-width) solid var(--color-border);
    font-size: var(--text-sm);
  }

  /* Each row's real DOM parent is ContextMenu's (invisible) wrapper, so every
   * row is a `:first-child` and the plain rule would erase every separator.
   * The first row is the first *wrapper* under the list. */
  .events > :global(:first-child .event) {
    border-top: none;
  }

  .event:hover {
    background: var(--color-surface-hover);
  }

  .message {
    color: var(--color-text-muted);
    overflow-wrap: anywhere;
  }
</style>
