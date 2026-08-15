<script>
  /**
   * The only clickable affordance in the UI.
   *
   * Always a real `<button>`: it comes with keyboard activation, the right
   * cursor, form semantics and screen-reader role for free, and every attempt
   * to rebuild that on a `<div>` gets some of it wrong. `tone` says how loud
   * the action is, never what it does -- at most one `accent` button is visible
   * in a view, which is what makes it mean anything.
   */
  let {
    tone = "neutral",
    size = "md",
    type = "button",
    disabled = false,
    title = undefined,
    onclick = undefined,
    children,
    ...rest
  } = $props();
</script>

<button
  {type}
  {disabled}
  {title}
  {onclick}
  class="button tone-{tone} size-{size}"
  {...rest}
>
  {@render children?.()}
</button>

<style>
  .button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    border: var(--border-width) solid transparent;
    border-radius: var(--radius-md);
    font-weight: var(--weight-medium);
    white-space: nowrap;
    transition:
      background-color var(--motion-fast) var(--easing-standard),
      border-color var(--motion-fast) var(--easing-standard),
      color var(--motion-fast) var(--easing-standard);
  }

  .size-sm {
    /* Padding is asymmetric on purpose: optical centring of a cap-height label
     * needs slightly less space below than the box maths suggests. */
    padding: var(--space-1) var(--space-2);
    font-size: var(--text-xs);
  }

  .size-md {
    padding: var(--space-2) var(--space-3);
    font-size: var(--text-base);
  }

  .tone-neutral {
    background: var(--color-surface);
    border-color: var(--color-border-strong);
    color: var(--color-text);
  }

  .tone-neutral:hover:not(:disabled) {
    background: var(--color-surface-hover);
  }

  .tone-neutral:active:not(:disabled) {
    background: var(--color-surface-active);
  }

  .tone-accent {
    background: var(--color-accent);
    color: var(--color-accent-text);
  }

  .tone-accent:hover:not(:disabled) {
    background: var(--color-accent-hover);
  }

  .tone-quiet {
    color: var(--color-text-muted);
  }

  .tone-quiet:hover:not(:disabled) {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .tone-danger {
    background: var(--color-surface);
    border-color: var(--color-border-strong);
    color: var(--color-danger);
  }

  .tone-danger:hover:not(:disabled) {
    background: var(--color-danger-subtle);
    border-color: var(--color-danger);
  }

  .button:disabled {
    /* Dimmed, not hidden, and the cursor says why. Removing a disabled control
     * from the flow makes the UI jump; making it look enabled is a lie. */
    opacity: 0.45;
    cursor: not-allowed;
  }
</style>
