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
   * **The mirror is in the flow; the textarea floats on it.** Not the other way
   * round, and this is the whole layout: the mirror's text is what gives the
   * component its height, so the editor is as tall as the program it holds and
   * needs nothing from its parent. Both layers absolutely positioned was the
   * first attempt, and it collapsed to zero height everywhere the parent chain
   * had no definite height -- text present in the DOM, nothing on screen, and an
   * alignment check that passed because it was comparing two empty rectangles.
   * It also means there is exactly one scroller (the card), so there is no
   * scroll to synchronise and no horizontal drift to fail to reproduce.
   *
   * Three traps remain, all with their fingerprints on the CSS below:
   *
   * - the textarea's glyphs are hidden with `-webkit-text-fill-color`, not
   *   `color`, because `color: transparent` also erases the *selection*, and a
   *   selection you cannot see is worse than no highlighting at all;
   * - a trailing newline needs a trailing character in the mirror, or the last
   *   line collapses and the textarea is a line taller than what it covers;
   * - the caret gets an explicit colour: it is the only part of the textarea
   *   that must stay visible.
   *
   * Read-only, this is still the right component: the mirror is the thing that
   * shows state, and the textarea underneath it keeps selection working, which
   * is how a sub-expression will be asked about (doc/dev/ui-cards.md section 6).
   */
  import { classOf, decorate } from "./decorate.js";
  import { INTERACTION } from "./interaction.js";

  let {
    /** The program text. */
    value = "",
    /** name -> node hash, as the server compiled this document. */
    bindings = {},
    /** hash -> state. A function, so this component subscribes to nothing. */
    stateOf = () => "unknown",
    /** True when this card has the keyboard.
     *
     * With `INTERACTION` at "click", this stops gating whether the text can be
     * typed into: a click lands the caret where it landed, which is what every
     * editor on the machine does. It still says which card the board's own
     * chords must keep their hands off. */
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

  /** True while the caret is in here. The thing that decides whose text is on
   * screen -- not a mode, because in "click" interaction there is no moment
   * anybody opened anything, and the mirror still has to show what is being
   * typed. */
  let focused = $state(false);

  /** What the mirror shows: the draft while somebody is typing into it, the
   * document the rest of the time. */
  const shown = $derived(focused || editing ? draft : value);

  // The document moved while nobody was typing -- somebody else's edit, an
  // undo, another file. Following it here is safe precisely because nobody is
  // typing; following it while they were would overwrite them mid-word.
  $effect(() => {
    if (!focused && !editing) draft = value;
  });
  const spans = $derived(decorate(shown, bindings, stateOf));

  $effect(() => {
    if (!editing) return;
    draft = value;
    queueMicrotask(() => {
      if (!field) return;
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
    });
  });

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
    readonly={INTERACTION === "focus" && !editing}
    aria-label="Program text"
    onkeydown={onKeydown}
    onselect={report}
    onkeyup={report}
    onmouseup={report}
    onfocus={() => (focused = true)}
    onblur={() => {
      focused = false;
      // Whatever was typed is kept, whether or not anything "opened" first: in
      // click interaction the edit never had a beginning to speak of, and an
      // editor that quietly discarded it would be the worst possible answer.
      if (draft !== value) oncommit?.(draft);
    }}
  ></textarea>
</div>

<style>
  .source {
    position: relative;
    width: 100%;
    /* No height of its own: the mirror below is in the flow and supplies it.
       Asking a parent for one is what made this collapse to nothing. */
    min-height: 100%;
  }

  /* THE CONTRACT. Everything here decides where a glyph lands, so it is
     declared once and both layers inherit it. Setting any of these on one
     layer alone is the bug this component exists to not have. */
  .layer {
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
    /* Neither layer scrolls: the card does. One scroller means nothing to keep
       in step, which is one whole class of bug that cannot happen here. */
    overflow: hidden;
  }

  /* In the flow, painted, and never interactive: it is both the picture and the
     ruler, and every event belongs to the textarea above it. */
  .mirror {
    position: relative;
    min-height: 100%;
    color: var(--color-text);
    pointer-events: none;
  }

  .field {
    position: absolute;
    inset: 0;
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

  /* Typing straight in: the caret is real before anything was "opened", so it
     has to be visible before anything was opened too. */
  :global([data-interaction="click"]) .field.inert {
    caret-color: var(--color-accent);
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
