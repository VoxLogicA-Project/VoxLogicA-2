<script>
  /**
   * The application shell, deliberately empty.
   *
   * Nothing goes here until it is the thing a user of VoxLogicA came for. The
   * state of the machinery -- instance, run, event log -- is a development
   * instrument and lives in the dev page's Debug section, reachable from the
   * Design menu; putting it on the main page would have shipped a debugger as
   * the product.
   *
   * The one exception is the build-error overlay. It is not app content: it is
   * the page telling you why it stopped updating, and it has to appear wherever
   * you are when the UI's own sources break.
   */
  import BuildError from "./lib/BuildError.svelte";
  import { app } from "./lib/state.svelte.js";
</script>

<main>
  {#if app.buildError}
    <BuildError error={app.buildError} />
  {/if}
</main>

<style>
  main {
    max-width: 52rem;
    margin: 0 auto;
    padding: var(--space-6) var(--space-5) var(--space-8);
  }
</style>
