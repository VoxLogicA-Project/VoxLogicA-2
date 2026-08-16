<script>
  /**
   * The program, with the state of every name written into the name.
   *
   * A `<textarea>` cannot colour an identifier, and nothing but a `<textarea>`
   * gets caret, selection, IME, undo and paste right for free. So there are two
   * layers: a mirror that renders the spans, and a transparent textarea exactly
   * on top of it. You type into something invisible and read something else, and
   * the whole component is the promise that you cannot tell.
   *
   * **The metrics contract.** Every property that affects where a glyph lands is
   * declared once, on `.layer`, and inherited by both layers. Never set one of
   * these on the textarea or the mirror alone -- font, size, line height, letter
   * spacing, tab size, wrapping, padding, border width, box sizing. A divergence
   * of one pixel is invisible at the top of a card and a line and a half out by
   * the bottom, which is exactly where nobody looks when they check.
   *
   * Four traps, all of which have their fingerprints on the CSS below:
   *
   * - the mirror never scrolls itself; it is *moved* to follow the textarea, on
   *   both axes, because a horizontal drift is the one people fail to reproduce;
   * - the textarea's glyphs are hidden with `-webkit-text-fill-color`, not
   *   `color`, because `color: transparent` also erases the *selection*, and a
   *   selection you cannot see is worse than no highlighting at all;
   * - a trailing newline needs a trailing space in the mirror, or the last line
   *   collapses and everything below it sits a line too high;
   * - the caret gets an explicit colour: it is the only part of the textarea
   *   that must stay visible.
   *
   * Read-only, this is still the right component: the mirror is the thing that
   * shows state, and the textarea underneath it keeps selection working, which
   * is how a sub-expression will be asked about (doc/dev/ui-cards.md section 6).
   */
  import { classOf, decorate } from "./decorate.js";

  let {
    /** The program text. */
    value = "",
    /** name -> node hash, as the server compiled this document. */
    bindings = {},
    /** hash -> state. A function, so this component subscribes to nothing. */
    stateOf = () => "unknown",
    /** True when this card has the keyboard. */
    editing = false,
    /** `(text)` — the edit was kept. */
    oncommit,
    /** The edit was abandoned. */
    oncancel,
    /** `({start, end, text})` — the selection moved while editing. */
    onselect,
  } = $props();

  let field = $state(null);
  let mirror = $state(null);
  // Seeded once and replaced when editing starts. The prop is deliberately not
  // followed after that: following it would overwrite what somebody is halfway
  // through typing, every time the workspace publishes.
  // svelte-ignore state_referenced_locally
  let draft = $state(value);

  /** What the mirror shows: the draft while typing, the document otherwise. */
  const shown = $derived(editing ? draft : value);
  const spans = $derived(decorate(shown, bindings, stateOf));

  $effect(() => {
    if (!editing) return;
    draft = value;
    queueMicrotask(() => {
      if (!field) return;
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
      sync();
    });
  });

  /** Keep the mirror under the textarea, on both axes. */
  function sync() {
    if (!field || !mirror) return;
    mirror.scrollTop = field.scrollTop;
    mirror.scrollLeft = field.scrollLeft;
  }

  function report() {
    if (!field || !onselect) return;
    const { selectionStart: start, selectionEnd: end } = field;
    onselect({ start, end, text: field.value.slice(start, end) });
  }

  function onKeydown(event) {
    // While a card has the keyboard, the board's shortcuts are somebody else's
    // business.
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

<div class="source" class:editing>
  <!-- Order matters: the mirror is painted, the textarea sits on top of it and
       takes every event. -->
  <pre bind:this={mirror} class="layer mirror" aria-hidden="true">{#each spans as span, i (i)}<span
        class={classOf(span)}>{span.text}</span>{/each}{"​"}</pre>

  <textarea
    bind:this={field}
    bind:value={draft}
    class="layer field"
    class:inert={!editing}
    spellcheck="false"
    autocomplete="off"
    autocapitalize="off"
    autocorrect="off"
    readonly={!editing}
    aria-label="Program text"
    onscroll={sync}
    oninput={sync}
    onkeydown={onKeydown}
    onselect={report}
    onkeyup={report}
    onmouseup={report}
    onblur={() => editing && oncommit?.(draft)}
  ></textarea>
</div>

<style>
  .source {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 0;
  }

  /* THE CONTRACT. Everything here decides where a glyph lands, so it is
     declared once and both layers inherit it. Setting any of these on one
     layer alone is the bug this component exists to not have. */
  .layer {
    position: absolute;
    inset: 0;
    margin: 0;
    padding: 0;
    border: 0;
    box-sizing: border-box;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    line-height: var(--leading-normal);
    letter-spacing: normal;
    tab-size: 2;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: normal;
    text-align: left;
    overflow: auto;
  }

  /* Painted, and never interactive: every event belongs to the textarea. */
  .mirror {
    color: var(--color-text);
    /* It is moved by `sync`, not scrolled by the user, and a scrollbar here
       would be a second one beside the textarea's. */
    overflow: hidden;
    pointer-events: none;
  }

  .field {
    background: none;
    resize: none;
    /* Not `color: transparent`: that erases the selection highlight too, and a
       selection you cannot see is worse than no colours at all. */
    -webkit-text-fill-color: transparent;
    color: transparent;
    /* The one part of this layer that must stay visible. */
    caret-color: var(--color-accent);
    /* No focus ring: the card is already ringed as the selected one, and a
       second ring inside the first is two answers to one question. */
    outline: none;
  }

  /* Not being edited: the textarea is still there, so selecting text still
     works and still reports, but it cannot take the caret by being clicked. */
  .field.inert {
    caret-color: transparent;
  }

  /* The vocabulary of state, written into the names themselves. A badge beside
     a name would be a second thing to read; the name is already there and
     already the thing being asked about. */
  .mirror :global(.tok.comment) {
    color: var(--color-text-subtle);
  }

  .mirror :global(.tok.string) {
    color: var(--color-text-muted);
  }

  .mirror :global(.tok.keyword) {
    color: var(--color-text-muted);
    font-weight: var(--weight-medium);
  }

  .mirror :global(.tok.binding) {
    /* Underlined rather than boxed: an underline costs no horizontal space, so
       adding it cannot move a single glyph out from under the textarea. */
    text-decoration: underline;
    text-decoration-style: dotted;
    text-underline-offset: 0.2em;
    text-decoration-color: var(--color-border);
  }

  .mirror :global(.tok.binding.is-pending) {
    text-decoration-color: var(--color-text-muted);
  }

  .mirror :global(.tok.binding.is-computing) {
    text-decoration-style: solid;
    text-decoration-color: var(--color-accent);
  }

  .mirror :global(.tok.binding.is-done) {
    color: var(--color-accent);
    text-decoration-style: solid;
    text-decoration-color: var(--color-accent);
  }

  .mirror :global(.tok.binding.is-failed) {
    color: var(--color-danger);
    text-decoration-style: wavy;
    text-decoration-color: var(--color-danger);
  }
</style>
