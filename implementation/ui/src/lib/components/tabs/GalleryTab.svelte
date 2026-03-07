<script>
  import { createEventDispatcher } from "svelte";
  import { getGallery } from "$lib/api/client.js";

  export let active = false;

  const dispatch = createEventDispatcher();

  let examples = [];
  let modules = ["all"];
  let moduleFilter = "all";
  let error = "";
  let loading = false;

  $: filtered = examples.filter((example) => moduleFilter === "all" || example.module === moduleFilter);

  const refresh = async () => {
    loading = true;
    try {
      const payload = await getGallery();
      examples = payload.examples || [];
      modules = ["all", ...(payload.modules || [])];
      if (!modules.includes(moduleFilter)) {
        moduleFilter = "all";
      }
      error = "";
    } catch (err) {
      examples = [];
      modules = ["all"];
      error = `Unable to load gallery: ${err.message}`;
    } finally {
      loading = false;
    }
  };

  const openExample = (example, run = false) => {
    dispatch("load", { code: example.code, run });
  };

  $: if (active) {
    refresh();
  }
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-gallery">
  <article class="card gallery-shell">
    <header class="gallery-topbar">
      <div class="gallery-topbar-copy">
        <h2>Examples</h2>
        <p>{filtered.length} focused programs, ready to open in Start.</p>
      </div>
      <div class="gallery-topbar-controls">
        <label class="gallery-filter">
          <span>Module</span>
          <select class="select" bind:value={moduleFilter}>
            {#each modules as moduleName}
              <option value={moduleName}>{moduleName}</option>
            {/each}
          </select>
        </label>
      </div>
    </header>

    {#if error}
      <div class="inline-error">{error}</div>
    {:else if loading && !examples.length}
      <div class="gallery-empty">Loading examples…</div>
    {:else if !filtered.length}
      <div class="gallery-empty">No examples for this module.</div>
    {:else}
      <div id="galleryCards" class="gallery-grid gallery-grid--polished">
        {#each filtered as example}
          <article class="gallery-card">
            <div class="gallery-card-meta">
              <span class="gallery-pill">{example.module}</span>
              <span class="gallery-pill gallery-pill--soft">{example.level}</span>
              <span class="gallery-pill gallery-pill--soft">{example.strategy || "dask"}</span>
            </div>
            <div class="gallery-card-copy">
              <h3>{example.title}</h3>
              <p>{example.description || ""}</p>
            </div>
            <pre class="gallery-code gallery-code--compact">{example.code}</pre>
            <div class="gallery-card-actions">
              <button class="btn btn-primary btn-small" type="button" on:click={() => openExample(example, false)}>
                Open in Start
              </button>
              <button class="btn btn-ghost btn-small" type="button" on:click={() => openExample(example, true)}>
                Open and Run
              </button>
            </div>
          </article>
        {/each}
      </div>
    {/if}
  </article>
</section>

<style>
  .gallery-shell {
    height: 100%;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 0.75rem;
  }

  .gallery-topbar {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 1rem;
    padding-bottom: 0.15rem;
    border-bottom: 1px solid rgba(16, 28, 45, 0.08);
  }

  .gallery-topbar-copy h2 {
    margin: 0;
    font-size: 1rem;
  }

  .gallery-topbar-copy p {
    margin: 0.18rem 0 0;
    color: var(--text-muted);
    font-size: 0.76rem;
  }

  .gallery-topbar-controls {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .gallery-filter {
    display: grid;
    gap: 0.2rem;
    color: var(--text-muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .gallery-grid--polished {
    margin-top: 0;
    align-content: start;
    overflow: auto;
    padding-right: 0.15rem;
  }

  .gallery-card {
    border: 1px solid rgba(16, 28, 45, 0.08);
    border-radius: 12px;
    background: linear-gradient(170deg, rgba(255, 255, 255, 0.95), rgba(244, 248, 253, 0.92));
    box-shadow: inset 0 1px rgba(255, 255, 255, 0.8), 0 12px 24px rgba(16, 28, 45, 0.08);
    padding: 0.8rem;
    display: grid;
    gap: 0.6rem;
  }

  .gallery-card-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .gallery-pill {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    padding: 0.18rem 0.42rem;
    background: rgba(20, 184, 166, 0.14);
    color: #0c6b61;
    font-size: 0.64rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .gallery-pill--soft {
    background: rgba(63, 116, 255, 0.1);
    color: #365480;
  }

  .gallery-card-copy {
    display: grid;
    gap: 0.28rem;
  }

  .gallery-card-copy h3 {
    margin: 0;
    font-size: 0.92rem;
    font-family: var(--font-display);
    letter-spacing: -0.015em;
  }

  .gallery-card-copy p {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.74rem;
    line-height: 1.45;
  }

  .gallery-code--compact {
    margin: 0;
    max-height: 220px;
    min-height: 160px;
    background: rgba(1, 10, 18, 0.82);
    color: #d9ecff;
    border: 1px solid rgba(16, 28, 45, 0.14);
    border-radius: 10px;
    padding: 0.72rem;
    font-size: 0.7rem;
    line-height: 1.42;
  }

  .gallery-card-actions {
    display: flex;
    gap: 0.42rem;
    flex-wrap: wrap;
  }

  .gallery-empty {
    display: grid;
    place-items: center;
    min-height: 220px;
    color: var(--text-muted);
    font-size: 0.82rem;
  }
</style>
