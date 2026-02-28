<script>
  import VoxCodeEditor from "./VoxCodeEditor.svelte";

  export let onSymbolClick = () => {};

  let value = "x = 1";
  const symbols = { x: "deadbeef" };
</script>

<VoxCodeEditor bind:value {symbols} diagnostics={[]} on:symbolclick={(event) => onSymbolClick(event.detail)} />
