<script>
  import { createEventDispatcher, onMount } from "svelte";
  import { getGallery } from "$lib/api/client.js";

  export let active = false;

  const dispatch = createEventDispatcher();

  let examples = [];
  let modules = ["all"];
  let moduleFilter = "all";
  let error = "";

  $: filtered = examples.filter((example) => moduleFilter === "all" || example.module === moduleFilter);

  const refresh = async () => {
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
    }
  };

  const loadExample = (example) => {
    dispatch("load", { code: example.code, run: false });
  };

  const runExample = (example) => {
    dispatch("load", { code: example.code, run: true });
  };

  $: if (active) {
    refresh();
  }

  onMount(() => {
    refresh();
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-gallery">
  <article class="card">
    <div class="card-header">
      <h2>Progressive Example Gallery</h2>
      <div class="row gap-s">
        <label class="inline-control" for="moduleFilter">Module</label>
        <select id="moduleFilter" class="select" bind:value={moduleFilter}>
          {#each modules as moduleName}
            <option value={moduleName}>{moduleName}</option>
          {/each}
        </select>
      </div>
    </div>

    <p class="muted">
      Gallery cards are generated from markdown directives in
      <code>doc/user/language-gallery.md</code> using
      <code>&lt;!-- vox:playground ... --&gt;</code> comments.
    </p>

    <div id="galleryCards" class="gallery-grid">
      {#if error}
        <div class="muted">{error}</div>
      {:else if !filtered.length}
        <div class="muted">No examples for selected module.</div>
      {:else}
        {#each filtered as example}
          <article class="gallery-item">
            <h3>{example.title}</h3>
            <div class="gallery-meta">
              <span class="mini-chip">{example.module}</span>
              <span class="mini-chip">{example.level}</span>
              <span class="mini-chip">{example.strategy || "dask"}</span>
            </div>
            <p class="muted">{example.description || ""}</p>
            <pre class="gallery-code">{example.code}</pre>
            <div class="row gap-s">
              <button class="btn btn-primary btn-small" type="button" on:click={() => loadExample(example)}>Load</button>
              <button class="btn btn-ghost btn-small" type="button" on:click={() => runExample(example)}>Run</button>
            </div>
          </article>
        {/each}
      {/if}
    </div>
  </article>
</section>
