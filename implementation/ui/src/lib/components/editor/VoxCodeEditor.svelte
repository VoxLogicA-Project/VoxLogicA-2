<script>
  import { createEventDispatcher, onDestroy, tick } from "svelte";
  import {
    applyCompletion,
    buildDefaultCompletions,
    buildOverlayHtml,
    completionContextAt,
    expressionContextAt,
    extractTokenInfoAt,
    getCaretCoordinates,
    isOperatorToken,
    textIndexFromPoint,
    VOX_KEYWORDS,
  } from "$lib/utils/vox-editor.js";

  export let value = "";
  export let symbols = {};
  export let diagnostics = [];
  export let autocompleteEnabled = true;
  export let completionProvider = null;
  export let completionKeywords = VOX_KEYWORDS;
  export let completionBuiltins = [];
  export let ariaLabel = "Program editor";
  export let placeholder = "";
  export let readonly = false;

  const dispatch = createEventDispatcher();

  let textareaEl;
  let overlayEl;
  let hoverTimer = null;
  let completionTimer = null;
  let completionRequestToken = 0;

  let overlayHtml = "";
  let suggestions = [];
  let selectedSuggestion = 0;
  let suggestionsOpen = false;
  let completionContext = null;
  let suggestionsPos = { left: 0, top: 0 };

  const clearSuggestionState = () => {
    suggestions = [];
    selectedSuggestion = 0;
    suggestionsOpen = false;
    completionContext = null;
  };

  const syncOverlayScroll = () => {
    if (!overlayEl || !textareaEl) return;
    overlayEl.scrollTop = textareaEl.scrollTop;
    overlayEl.scrollLeft = textareaEl.scrollLeft;
  };

  const scheduleCompletion = () => {
    if (!autocompleteEnabled || readonly) return;
    if (completionTimer) clearTimeout(completionTimer);
    completionTimer = setTimeout(() => {
      void openCompletions(false);
    }, 80);
  };

  const toCompletionItems = (items, context) => {
    const rows = Array.isArray(items) ? items : [];
    const prefix = String(context?.prefix || "").toLowerCase();
    return rows
      .map((item) => {
        if (typeof item === "string") {
          return { label: item, insertText: item, kind: "symbol", detail: "" };
        }
        return {
          label: String(item?.label || ""),
          insertText: String(item?.insertText || item?.label || ""),
          kind: String(item?.kind || "symbol"),
          detail: String(item?.detail || ""),
        };
      })
      .filter((item) => item.label)
      .filter((item) => !prefix || item.label.toLowerCase().startsWith(prefix));
  };

  const openCompletions = async (forced) => {
    if (!autocompleteEnabled || readonly || !textareaEl) return;
    const cursor = Number(textareaEl.selectionStart || 0);
    const context = completionContextAt(value, cursor);
    if (!forced && !context.prefix) {
      clearSuggestionState();
      return;
    }

    const requestToken = completionRequestToken + 1;
    completionRequestToken = requestToken;

    let items = [];
    if (typeof completionProvider === "function") {
      try {
        items = await completionProvider({
          ...context,
          symbols: { ...(symbols || {}) },
        });
      } catch {
        items = [];
      }
    } else {
      items = buildDefaultCompletions(context, {
        symbols,
        keywords: completionKeywords,
        builtins: completionBuiltins,
      });
    }

    if (requestToken !== completionRequestToken) return;

    const normalized = toCompletionItems(items, context);
    if (!normalized.length) {
      clearSuggestionState();
      return;
    }

    const caret = getCaretCoordinates(textareaEl, context.cursor);
    suggestions = normalized;
    selectedSuggestion = 0;
    suggestionsOpen = true;
    completionContext = context;
    suggestionsPos = {
      left: Math.max(8, Math.round(caret.left + 8)),
      top: Math.max(8, Math.round(caret.top + caret.height + 6)),
    };
    dispatch("completionstate", {
      open: true,
      count: suggestions.length,
      prefix: context.prefix,
    });
  };

  const applyActiveSuggestion = async () => {
    if (!suggestionsOpen || !suggestions.length || !completionContext) return false;
    const item = suggestions[Math.max(0, Math.min(selectedSuggestion, suggestions.length - 1))];
    const applied = applyCompletion(value, completionContext, item);
    value = applied.text;
    dispatch("change", { value });
    clearSuggestionState();
    await tick();
    if (textareaEl) {
      textareaEl.focus();
      textareaEl.selectionStart = applied.cursor;
      textareaEl.selectionEnd = applied.cursor;
    }
    dispatch("completionapply", { item, cursor: applied.cursor });
    return true;
  };

  const handleInput = () => {
    dispatch("change", { value });
    dispatch("input", { value });
    scheduleCompletion();
  };

  const handleKeydown = async (event) => {
    if ((event.ctrlKey || event.metaKey) && String(event.key || "").toLowerCase() === " ") {
      event.preventDefault();
      await openCompletions(true);
      return;
    }

    if (!suggestionsOpen) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      selectedSuggestion = (selectedSuggestion + 1) % suggestions.length;
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      selectedSuggestion = (selectedSuggestion - 1 + suggestions.length) % suggestions.length;
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      clearSuggestionState();
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      await applyActiveSuggestion();
    }
  };

  const handleOverlayMouseOver = (event) => {
    const symbolEl = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!symbolEl) return;
    const token = decodeURIComponent(String(symbolEl.getAttribute("data-token") || ""));
    if (!token) return;
    dispatch("symbolhover", { token });
  };

  const handleOverlayMouseOut = (event) => {
    const from = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!from) return;
    const to = event.relatedTarget instanceof Element ? event.relatedTarget.closest(".vx-editor__symbol") : null;
    if (to) return;
    dispatch("symbolleave");
  };

  const handleOverlayFocus = () => {};

  const handleOverlayBlur = () => {
    dispatch("symbolleave");
  };

  const handleOverlayClick = (event) => {
    const symbolEl = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!symbolEl) return;
    event.preventDefault();
    const token = decodeURIComponent(String(symbolEl.getAttribute("data-token") || ""));
    if (!token) return;
    dispatch("symbolclick", { token });
    textareaEl?.focus({ preventScroll: true });
  };

  const handleMouseMove = (event) => {
    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      const position = textIndexFromPoint(textareaEl, event.clientX, event.clientY);
      if (!Number.isInteger(position)) {
        dispatch("hoverleave");
        return;
      }
      const info = extractTokenInfoAt(value, position);
      if (!info.token) {
        dispatch("hoverleave");
        return;
      }
      if (symbols && symbols[info.token]) {
        return;
      }
      if (!isOperatorToken(info.token)) {
        dispatch("hoverleave");
        return;
      }
      dispatch("operatorhover", {
        token: info.token,
        context: expressionContextAt(value, info.start),
      });
    }, 100);
  };

  const handleMouseLeave = () => {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    dispatch("hoverleave");
  };

  $: overlayHtml = buildOverlayHtml(value, symbols, diagnostics);
  $: if (!suggestionsOpen && suggestions.length) {
    suggestions = [];
  }

  const chooseSuggestion = async (index) => {
    selectedSuggestion = Number(index || 0);
    await applyActiveSuggestion();
  };

  const clearTimers = () => {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    if (completionTimer) {
      clearTimeout(completionTimer);
      completionTimer = null;
    }
  };

  onDestroy(() => {
    clearTimers();
  });
</script>

<div class="vx-editor" data-testid="vox-code-editor">
  <div class="vx-editor__shell">
    <textarea
      class="vx-editor__input"
      aria-label={ariaLabel}
      bind:this={textareaEl}
      bind:value
      spellcheck="false"
      {placeholder}
      {readonly}
      on:input={handleInput}
      on:keydown={handleKeydown}
      on:mousemove={handleMouseMove}
      on:mouseleave={handleMouseLeave}
      on:scroll={syncOverlayScroll}
    ></textarea>
    <pre
      class="vx-editor__overlay"
      bind:this={overlayEl}
      aria-hidden="true"
      role="presentation"
      tabindex="-1"
      on:mouseover={handleOverlayMouseOver}
      on:mouseout={handleOverlayMouseOut}
      on:click={handleOverlayClick}
      on:focus={handleOverlayFocus}
      on:blur={handleOverlayBlur}
    >{@html overlayHtml}</pre>
  </div>

  {#if suggestionsOpen && suggestions.length}
    <ul
      class="vx-editor__suggestions"
      role="listbox"
      aria-label="Autocomplete suggestions"
      style={`left:${suggestionsPos.left}px;top:${suggestionsPos.top}px`}
      data-testid="completion-list"
    >
      {#each suggestions as item, idx}
        <li
          role="option"
          aria-selected={idx === selectedSuggestion}
          class={`vx-editor__suggestion ${idx === selectedSuggestion ? "is-selected" : ""}`}
          on:mousedown|preventDefault
        >
          <button
            type="button"
            class="vx-editor__suggestion-button"
            on:click={() => chooseSuggestion(idx)}
          >
            <span class="vx-editor__suggestion-label">{item.label}</span>
            {#if item.kind}
              <span class="vx-editor__suggestion-kind">{item.kind}</span>
            {/if}
            {#if item.detail}
              <span class="vx-editor__suggestion-detail">{item.detail}</span>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<svelte:window on:blur={clearSuggestionState} on:beforeunload={clearTimers} />

<style>
  .vx-editor {
    position: relative;
    width: 100%;
  }

  .vx-editor__shell {
    position: relative;
  }

  .vx-editor__input {
    width: 100%;
    min-height: 300px;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    resize: vertical;
    background: rgba(1, 10, 18, 0.8);
    color: transparent;
    caret-color: #cce2ff;
    -webkit-text-fill-color: transparent;
    padding: 0.8rem;
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.83rem;
    line-height: 1.5;
    tab-size: 2;
    font-variant-ligatures: none;
    font-feature-settings: "liga" 0, "calt" 0;
    transition: border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
  }

  .vx-editor__input:focus {
    outline: 2px solid rgba(38, 198, 169, 0.35);
    outline-offset: 1px;
  }

  .vx-editor__overlay {
    position: absolute;
    inset: 0;
    margin: 0;
    padding: 0.8rem;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    overflow: hidden;
    pointer-events: none;
    white-space: pre-wrap;
    word-break: normal;
    overflow-wrap: normal;
    color: transparent;
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.83rem;
    line-height: 1.5;
    tab-size: 2;
    font-variant-ligatures: none;
    font-feature-settings: "liga" 0, "calt" 0;
  }

  :global(.vx-editor__line) {
    display: block;
    min-height: 1.5em;
  }

  :global(.vx-editor__line--error) {
    background: rgba(240, 93, 94, 0.16);
    box-shadow: inset 3px 0 0 rgba(240, 93, 94, 0.75);
  }

  :global(.vx-editor__token--keyword) {
    color: #8ef4df;
  }

  :global(.vx-editor__token--number) {
    color: #f7c26a;
  }

  :global(.vx-editor__token--string) {
    color: #c5f0a6;
  }

  :global(.vx-editor__token--comment) {
    color: #8ea6ba;
  }

  :global(.vx-editor__token--operator) {
    color: #b9cae2;
  }

  :global(.vx-editor__token--identifier) {
    color: #cce2ff;
  }

  :global(.vx-editor__symbol) {
    pointer-events: auto;
    border: none;
    border-radius: 0;
    background: transparent;
    color: #8cecdc;
    padding: 0;
    margin: 0;
    font: inherit;
    line-height: inherit;
    cursor: pointer;
    text-decoration-line: underline;
    text-decoration-color: rgba(47, 215, 185, 0.45);
    text-decoration-thickness: 1px;
    text-underline-offset: 2px;
    transition: color 120ms ease, text-decoration-color 120ms ease;
  }

  :global(.vx-editor__symbol:hover) {
    color: #d8fff7;
    text-decoration-color: rgba(47, 215, 185, 0.95);
  }

  :global(.vx-editor__symbol:focus-visible) {
    outline: 1px solid rgba(47, 215, 185, 0.45);
    outline-offset: 1px;
    border-radius: 2px;
  }

  .vx-editor__suggestions {
    position: fixed;
    z-index: 80;
    margin: 0;
    padding: 0.3rem;
    list-style: none;
    min-width: 240px;
    max-width: min(460px, calc(100vw - 24px));
    max-height: 260px;
    overflow: auto;
    border-radius: var(--radius-sm);
    border: 1px solid var(--line);
    background: rgba(8, 20, 33, 0.97);
    box-shadow: 0 18px 30px rgba(3, 8, 14, 0.52);
  }

  .vx-editor__suggestion {
    padding: 0;
    border-radius: 0.45rem;
  }

  .vx-editor__suggestion-button {
    width: 100%;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.3rem 0.5rem;
    align-items: baseline;
    padding: 0.36rem 0.45rem;
    border-radius: 0.45rem;
    color: #d9ecff;
    cursor: pointer;
    font-size: 0.75rem;
    font-family: "IBM Plex Mono", monospace;
    border: none;
    background: transparent;
    text-align: left;
  }

  .vx-editor__suggestion.is-selected .vx-editor__suggestion-button {
    background: rgba(38, 198, 169, 0.2);
    outline: 1px solid rgba(38, 198, 169, 0.4);
  }

  .vx-editor__suggestion-label {
    font-weight: 700;
  }

  .vx-editor__suggestion-kind {
    color: #94c8bd;
    text-transform: uppercase;
    font-size: 0.64rem;
    letter-spacing: 0.08em;
  }

  .vx-editor__suggestion-detail {
    grid-column: 1 / -1;
    color: #95aac0;
    font-size: 0.68rem;
  }
</style>
