<script>
  /**
   * A node, as a state rather than as a value.
   *
   * This is what a result card shows before it shows anything else, and often
   * all it will ever show: most nodes in a program are images and volumes, and
   * the honest thing to render for a three-hundred-megabyte array is what it is
   * and how big, not a preview nobody asked to download.
   *
   * The states are five and they are not decorative:
   *
   *   unknown    nobody has asked for this node, or nobody knows the name
   *   pending    it is in the plan and its turn has not come
   *   computing  a worker has it now
   *   done       there is a value; `summary` says what, `value` may hold it
   *   failed     there is an error, and the error is the result
   *
   * A card sitting at `pending` while a long run works through the plan is
   * showing the truth, and the truth is worth more than a spinner that implies
   * something is happening to *this* node.
   */
  let {
    /** A `Result` from the results store. */
    result = { state: "unknown" },
    /** The binding the card names, for when there is nothing else to say. */
    node = "",
  } = $props();

  const LABEL = {
    unknown: "not computed",
    pending: "queued",
    computing: "computing",
    done: "",
    failed: "failed",
  };

  const label = $derived(LABEL[result.state] ?? result.state);
</script>

<div class="result" data-state={result.state}>
  <header>
    <span class="node">{node}</span>
    {#if label}
      <span class="state" class:working={result.state === "computing"}>{label}</span>
    {/if}
  </header>

  {#if result.state === "failed"}
    <!-- The error is the result, so it gets the room a value would have had. -->
    <pre class="error">{result.error ?? "no detail"}</pre>
  {:else if result.state === "done"}
    {#if result.value !== undefined && result.value !== null}
      <pre class="value">{format(result.value)}</pre>
    {:else if result.summary}
      <p class="summary">{result.summary}</p>
    {:else}
      <p class="summary">computed</p>
    {/if}
  {/if}
</div>

<script module>
  /** Numbers as themselves, everything else as JSON. A float printed to
   * seventeen places is a number nobody can read at a glance. */
  export function format(value) {
    if (typeof value === "number") {
      return Number.isInteger(value) ? String(value) : value.toPrecision(6);
    }
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 1);
    } catch {
      return String(value);
    }
  }
</script>

<style>
  .result {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    width: 100%;
    height: 100%;
    min-height: 0;
  }

  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-2);
  }

  .node {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text);
  }

  /* The state is said quietly. It is the frame around the value, not the point,
   * and a card that shouted its own status would be a card you read twice. */
  .state {
    font-size: var(--text-xs);
    color: var(--color-text-muted);
    white-space: nowrap;
  }

  /* The one exception: something is happening, and a still page cannot say so. */
  .working {
    animation: breathe var(--motion-pulse) var(--easing-standard) infinite;
  }

  @keyframes breathe {
    0%,
    100% {
      opacity: 0.4;
    }
    50% {
      opacity: 1;
    }
  }

  .value,
  .error,
  .summary {
    margin: 0;
    min-height: 0;
    overflow: auto;
    font-size: var(--text-xs);
    line-height: var(--leading-normal);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .value {
    font-family: var(--font-mono);
    color: var(--color-text);
  }

  .summary {
    color: var(--color-text-muted);
  }

  .error {
    font-family: var(--font-mono);
    color: var(--color-danger);
  }
</style>
