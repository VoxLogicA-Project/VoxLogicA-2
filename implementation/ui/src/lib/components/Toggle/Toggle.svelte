<script>
  /**
   * A binary setting that takes effect immediately.
   *
   * `role="switch"` with `aria-checked`, not a checkbox: a checkbox says "this
   * will be true when you submit", a switch says "this is true now". The label
   * is part of the control, so the whole row is the hit target -- a 20px track
   * is not something to ask a pointer to find.
   */
  let {
    checked = $bindable(false),
    disabled = false,
    label,
    description = undefined,
    onchange = undefined,
  } = $props();

  function toggle() {
    if (disabled) return;
    checked = !checked;
    onchange?.(checked);
  }
</script>

<button
  type="button"
  role="switch"
  aria-checked={checked}
  {disabled}
  class="row"
  onclick={toggle}
>
  <span class="text">
    <span class="label">{label}</span>
    {#if description}<span class="description">{description}</span>{/if}
  </span>
  <span class="track" aria-hidden="true"><span class="knob"></span></span>
</button>

<style>
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    width: 100%;
    padding: var(--space-2);
    margin: calc(var(--space-2) * -1);
    border-radius: var(--radius-md);
    text-align: left;
    transition: background-color var(--motion-fast) var(--easing-standard);
  }

  .row:hover:not(:disabled) {
    background: var(--color-surface-hover);
  }

  .row:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .text {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    min-width: 0;
  }

  .label {
    font-size: var(--text-base);
  }

  .description {
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  .track {
    flex: none;
    position: relative;
    width: 34px;
    height: 20px;
    border-radius: var(--radius-full);
    background: var(--color-border-strong);
    transition: background-color var(--motion-fast) var(--easing-standard);
  }

  .knob {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    border-radius: var(--radius-full);
    background: var(--color-surface);
    box-shadow: var(--shadow-sm);
    transition: transform var(--motion-fast) var(--easing-standard);
  }

  [aria-checked="true"] .track {
    background: var(--color-accent);
  }

  [aria-checked="true"] .knob {
    transform: translateX(14px);
  }
</style>
