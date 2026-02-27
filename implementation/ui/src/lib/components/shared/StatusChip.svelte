<script>
  import { statusChipClass } from "$lib/utils/format.js";

  export let value = "idle";
  export let extraClass = "";
</script>

<span class={`chip ${statusChipClass(value)} ${extraClass}`.trim()}>{String(value || "idle").toLowerCase()}</span>
