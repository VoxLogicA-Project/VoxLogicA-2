<script>
  export let visible = false;
  export let dissolving = false;
  export let nodeIds = [];
</script>

{#if visible}
  <div class={`start-compute-dream ${dissolving ? "is-dissolving" : ""}`}>
    <div class="start-compute-dream-grid">
      {#each nodeIds as nodeId, nodeIndex (`${nodeId}-${nodeIndex}`)}
        <span class="start-compute-dream-node" style={`--node-index:${nodeIndex}`}>
          {nodeId}
        </span>
      {/each}
    </div>
  </div>
{/if}
