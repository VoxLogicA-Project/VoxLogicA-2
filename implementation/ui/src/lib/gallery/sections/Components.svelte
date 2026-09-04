<script>
  /** The library. Every component, every state, live. */
  import { components } from "../registry.js";
  import Specimen from "../Specimen.svelte";
</script>

<p class="lede">
  This is the library, not a set of examples: each specimen mounts the same
  module the application imports, from the variant list the component ships
  beside itself. {components.length} components.
</p>

{#each components as entry (entry.name)}
  <Specimen {entry} />
{/each}

<style>
  .lede {
    margin-bottom: var(--space-6);
    color: var(--color-text-muted);
  }
</style>
