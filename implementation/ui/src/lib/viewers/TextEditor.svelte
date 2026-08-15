<script>
  /**
   * The provisional viewer: text, and a way to change it.
   *
   * Every card is shown by a *viewer*, chosen from what the card is (see
   * ./index.js). This is the only one so far and it is deliberately plain --
   * no syntax colour, no completion, no gutter -- because its job right now is
   * to establish the shape every other viewer will have: it renders what the
   * card holds, it says when it wants the keyboard, and it hands back text.
   * Everything richer is a different viewer, not a bigger one.
   *
   * Editing starts with the cursor at the end. A card you pressed Enter on is a
   * card you want to add to; putting the cursor at the start would mean typing
   * in front of what is already there, which is almost never the intent.
   */
  let {
    /** The text this card holds. */
    value = "",
    /** True when this card is the one being edited. */
    editing = false,
    /** Monospace for programs, prose for notes. */
    mono = true,
    /** `(text)` — the edit was kept. */
    oncommit,
    /** The edit was abandoned. */
    oncancel,
  } = $props();

  let field = $state(null);
  let draft = $state(value);

  $effect(() => {
    if (!editing) return;
    // A fresh draft each time editing starts, from whatever the card holds now:
    // reusing the last one would quietly resurrect an abandoned edit.
    draft = value;
    queueMicrotask(() => {
      if (!field) return;
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
      field.scrollTop = field.scrollHeight;
    });
  });

  function onKeydown(event) {
    // Everything here is deliberately local: while a card has the keyboard,
    // the board's own shortcuts are somebody else's business.
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      oncancel?.();
    } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      // Enter alone is a newline -- this is text. The chord is "done".
      event.preventDefault();
      oncommit?.(draft);
    }
  }
</script>

{#if editing}
  <textarea
    bind:this={field}
    bind:value={draft}
    class="field"
    class:mono
    spellcheck="false"
    aria-label="Card text"
    onkeydown={onKeydown}
    onblur={() => oncommit?.(draft)}
  ></textarea>
{:else}
  <pre class="text" class:mono>{value}</pre>
{/if}

<style>
  .text,
  .field {
    margin: 0;
    width: 100%;
    height: 100%;
    min-height: 0;
    font-size: var(--text-xs);
    line-height: var(--leading-normal);
    color: var(--color-text);
    /* Wrapped, not clipped: a card is a window onto text, and a long line that
     * disappears off the right edge is a line you did not know you had. */
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .mono {
    font-family: var(--font-mono);
  }

  .field {
    display: block;
    resize: none;
    border: none;
    background: none;
    /* No focus ring: the card is already ringed as the selected one, and a
     * second ring inside the first is two answers to one question. */
    outline: none;
  }
</style>
