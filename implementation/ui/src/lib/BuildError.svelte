<script>
  // Shown when the *UI's own* sources stopped compiling while the page was
  // open. The page keeps running (and keeps its WebSocket) so the next good
  // build can reload it; a build failure must never cost you a live session.
  let { error } = $props();
</script>

<div class="overlay">
  <strong>{error.summary}</strong>
  {#if error.detail}<pre>{error.detail}</pre>{/if}
</div>

<style>
  .overlay { border: 1px solid var(--bad); border-radius: 6px; padding: 1rem;
             margin-bottom: 2rem; background: color-mix(in srgb, var(--bad) 8%, transparent); }
  strong { color: var(--bad); display: block; margin-bottom: 0.5rem; }
  pre { margin: 0; white-space: pre-wrap; overflow-x: auto; font-size: 0.8rem; }
</style>
