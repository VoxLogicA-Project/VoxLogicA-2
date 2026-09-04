<script>
  // Shown when the *UI's own* sources stopped compiling while the page was
  // open. The page keeps running (and keeps its WebSocket) so the next good
  // build can reload it; a build failure must never cost you a live session.
  let { error } = $props();
</script>

<div class="overlay" role="alert">
  <strong>{error.summary}</strong>
  {#if error.detail}<pre>{error.detail}</pre>{/if}
</div>

<style>
  .overlay {
    padding: var(--space-4);
    border: var(--border-width) solid var(--color-danger);
    border-radius: var(--radius-lg);
    background: var(--color-danger-subtle);
  }

  strong {
    display: block;
    margin-bottom: var(--space-2);
    color: var(--color-danger);
    font-weight: var(--weight-semibold);
  }

  pre {
    margin: 0;
    white-space: pre-wrap;
    overflow-x: auto;
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }
</style>
