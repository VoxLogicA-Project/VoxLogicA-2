<script>
  export let items = [];
  export let valueField = "value";
  export let labelField = "label";
  export let formatter = (value) => `${value}`;
  export let emptyMessage = "No data yet.";

  $: maxValue = Math.max(1, ...items.map((item) => Number(item?.[valueField] || 0)));
</script>

{#if !items.length}
  <div class="muted">{emptyMessage}</div>
{:else}
  {#each items as item}
    {@const value = Number(item?.[valueField] || 0)}
    {@const pct = (value / maxValue) * 100}
    <div class="bar-row">
      <div class="meta">
        <span>{item?.[labelField]}</span>
        <strong>{formatter(value)}</strong>
      </div>
      <div class="bar-bg"><div class="bar-fill" style={`width: ${pct.toFixed(2)}%`}></div></div>
    </div>
  {/each}
{/if}
