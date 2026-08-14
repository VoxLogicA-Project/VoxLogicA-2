<script>
  import { app } from "./lib/state.svelte.js";
  import BuildError from "./lib/BuildError.svelte";

  const statusLabel = $derived(
    app.connection.status === "connected"
      ? `connected as ${app.connection.clientId ?? "?"}`
      : app.connection.status,
  );
</script>

<main>
  <header>
    <h1>VoxLogicA</h1>
    <span class="status {app.connection.status}">{statusLabel}</span>
  </header>

  {#if app.buildError}
    <BuildError error={app.buildError} />
  {/if}

  <section>
    <h2>Instance</h2>
    {#if app.instance}
      <dl>
        <dt>pid</dt><dd>{app.instance.pid}</dd>
        <dt>port</dt><dd>{app.instance.port}</dd>
        <dt>store</dt><dd>{app.instance.storeDb ?? "default"}</dd>
        <dt>mode</dt><dd>{app.instance.dev ? "dev (live reload)" : "shipped bundle"}</dd>
        <dt>clients</dt><dd>{app.instance.clients}</dd>
      </dl>
    {:else}
      <p class="muted">no instance information</p>
    {/if}
  </section>

  <section>
    <h2>Run</h2>
    <dl>
      <dt>program</dt><dd>{app.run.program ?? "—"}</dd>
      <dt>status</dt><dd>{app.run.status}{app.running ? " …" : ""}</dd>
      {#if app.nodeCount !== null}
        <dt>nodes</dt><dd>{app.nodeCount}</dd>
      {/if}
      {#if app.run.elapsed != null}
        <dt>elapsed</dt><dd>{app.run.elapsed.toFixed(2)}s</dd>
      {/if}
    </dl>
  </section>

  <section>
    <h2>Events</h2>
    {#if app.recentEvents.length === 0}
      <p class="muted">nothing yet</p>
    {:else}
      <ul class="events">
        {#each app.recentEvents as event, index (index)}
          <li><code>{event.type}</code> {event.message ?? ""}</li>
        {/each}
      </ul>
    {/if}
  </section>
</main>

<style>
  main { max-width: 52rem; margin: 0 auto; padding: 2rem 1.5rem; }
  header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 2rem; }
  h1 { font-size: 1.1rem; letter-spacing: 0.02em; margin: 0; }
  h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em;
       color: var(--muted); margin: 0 0 0.5rem; }
  section { margin-bottom: 2rem; }
  .status { font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 999px;
            border: 1px solid var(--border); color: var(--muted); }
  .status.connected { color: var(--ok); border-color: var(--ok); }
  .status.disconnected { color: var(--bad); border-color: var(--bad); }
  dl { display: grid; grid-template-columns: 8rem 1fr; gap: 0.25rem 1rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .muted { color: var(--muted); }
  .events { list-style: none; margin: 0; padding: 0; font-size: 0.85rem; }
  .events li { padding: 0.15rem 0; border-bottom: 1px solid var(--border); }
</style>
