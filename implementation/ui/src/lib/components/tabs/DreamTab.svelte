<script>
  import DreamVisual from "$lib/components/shared/DreamVisual.svelte";
  import { dreamState } from "$lib/stores/dreamStore.js";

  export let active = false;
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-dream">
  <article class="card dream-shell">
    <header class="dream-head">
      <h2>Oneiric Trace</h2>
      <p class="muted">Live materialization traces from the runtime.</p>
    </header>

    <div class="dream-stage">
      <DreamVisual visible={$dreamState.visible} dissolving={$dreamState.dissolving} nodeIds={$dreamState.nodeIds} />
      {#if !$dreamState.visible}
        <div class="dream-placeholder">
          No active trace. Run a program or click a value to trigger a dream sequence.
        </div>
      {/if}
    </div>
  </article>
</section>
