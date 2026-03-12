<script>
  import { afterUpdate, createEventDispatcher, onDestroy, tick } from "svelte";
  import {
    applyCompletion,
    buildDefaultCompletions,
    buildEditorDocument,
    caretClientRectFromSelection,
    completionContextAt,
    expressionContextAt,
    isOperatorToken,
    readEditableText,
    restoreSelectionWithin,
    selectionOffsetsWithin,
    VOX_KEYWORDS,
  } from "$lib/utils/vox-editor.js";

  export let value = "";
  export let symbols = {};
  export let diagnostics = [];
  export let symbolStatuses = {};
  export let selectedSymbols = [];
  export let symbolTypes = {};
  export let autocompleteEnabled = true;
  export let completionProvider = null;
  export let completionKeywords = VOX_KEYWORDS;
  export let completionBuiltins = [];
  export let ariaLabel = "Program editor";
  export let placeholder = "";
  export let readonly = false;

  const dispatch = createEventDispatcher();

  let editorEl;
  let hoverTimer = null;
  let completionTimer = null;
  let completionRequestToken = 0;
  let editorDocument = [];
  let editorText = "";
  let pendingSelection = null;
  let pendingScroll = null;
  let pendingFocus = false;

  let suggestions = [];
  let selectedSuggestion = 0;
  let suggestionsOpen = false;
  let completionContext = null;
  let suggestionsPos = { left: 0, top: 0 };

  const currentText = () => editorText;

  const currentSelection = () =>
    selectionOffsetsWithin(editorEl) || {
      anchor: currentText().length,
      focus: currentText().length,
      start: currentText().length,
      end: currentText().length,
      collapsed: true,
    };

  const preserveViewport = () => {
    if (!editorEl) return;
    pendingScroll = {
      left: editorEl.scrollLeft,
      top: editorEl.scrollTop,
    };
  };

  const queueSelectionRestore = (start, end = start, { focus = false } = {}) => {
    pendingSelection = { start, end };
    pendingFocus = pendingFocus || focus;
    preserveViewport();
  };

  const emitTextChange = (emitInput = true) => {
    dispatch("change", { value });
    if (emitInput) {
      dispatch("input", { value });
    }
  };

  const clearSuggestionState = (notify = false) => {
    const wasOpen = suggestionsOpen;
    suggestions = [];
    selectedSuggestion = 0;
    suggestionsOpen = false;
    completionContext = null;
    if (notify && wasOpen) {
      dispatch("completionstate", {
        open: false,
        count: 0,
        prefix: "",
      });
    }
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
    if (!autocompleteEnabled || readonly || !editorEl) return;
    const selection = currentSelection();
    const cursor = Number(selection.end || 0);
    const context = completionContextAt(currentText(), cursor);

    if (!forced && !context.prefix) {
      clearSuggestionState(true);
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
      clearSuggestionState(true);
      return;
    }

    const caret = caretClientRectFromSelection(editorEl) || editorEl.getBoundingClientRect();
    suggestions = normalized;
    selectedSuggestion = 0;
    suggestionsOpen = true;
    completionContext = context;
    suggestionsPos = {
      left: Math.max(8, Math.round(caret.left + 8)),
      top: Math.max(8, Math.round((caret.bottom || caret.top || 0) + 6)),
    };
    dispatch("completionstate", {
      open: true,
      count: suggestions.length,
      prefix: context.prefix,
    });
  };

  const applyTextEdit = async (nextText, cursor, { emitInput = true, focus = true } = {}) => {
    queueSelectionRestore(cursor, cursor, { focus });
    value = nextText;
    emitTextChange(emitInput);
    await tick();
  };

  const replaceSelection = async (insertText, { emitInput = true, focus = true } = {}) => {
    const source = currentText();
    const selection = currentSelection();
    const nextText = `${source.slice(0, selection.start)}${insertText}${source.slice(selection.end)}`;
    const nextCursor = selection.start + insertText.length;
    await applyTextEdit(nextText, nextCursor, { emitInput, focus });
  };

  const applyActiveSuggestion = async () => {
    if (!suggestionsOpen || !suggestions.length || !completionContext) return false;
    const item = suggestions[Math.max(0, Math.min(selectedSuggestion, suggestions.length - 1))];
    const applied = applyCompletion(currentText(), completionContext, item);
    clearSuggestionState(true);
    await applyTextEdit(applied.text, applied.cursor, { emitInput: false, focus: true });
    dispatch("completionapply", { item, cursor: applied.cursor });
    return true;
  };

  const handleInput = (event) => {
    if (!editorEl) return;
    const eventValue = typeof event?.target?.value === "string" ? event.target.value : null;
    const nextText = eventValue ?? readEditableText(editorEl);
    const selection = currentSelection();
    queueSelectionRestore(selection.start, selection.end, { focus: true });
    if (nextText !== currentText()) {
      value = nextText;
      emitTextChange(true);
    }
    scheduleCompletion();
  };

  const handleKeydown = async (event) => {
    if ((event.ctrlKey || event.metaKey) && String(event.key || "").toLowerCase() === " ") {
      event.preventDefault();
      await openCompletions(true);
      return;
    }

    if (suggestionsOpen) {
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
        clearSuggestionState(true);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        await applyActiveSuggestion();
        return;
      }
    }

    if (!readonly && event.key === "Tab" && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      await replaceSelection("  ");
    }
  };

  const handlePaste = async (event) => {
    if (readonly) return;
    const text = String(event.clipboardData?.getData("text/plain") || "");
    event.preventDefault();
    await replaceSelection(text);
  };

  const handleEditorMouseOver = (event) => {
    const symbolEl = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!symbolEl || !editorEl?.contains(symbolEl)) return;
    const token = String(symbolEl.getAttribute("data-token") || "");
    if (!token) return;
    dispatch("symbolhover", { token });
  };

  const handleEditorMouseOut = (event) => {
    const from = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!from) return;
    const to = event.relatedTarget instanceof Element ? event.relatedTarget.closest(".vx-editor__symbol") : null;
    if (to) return;
    dispatch("symbolleave");
  };

  const handleEditorMouseDown = (event) => {
    const symbolEl = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!symbolEl || !editorEl?.contains(symbolEl)) return;
    event.preventDefault();
  };

  const handleEditorClick = (event) => {
    const symbolEl = event.target instanceof Element ? event.target.closest(".vx-editor__symbol") : null;
    if (!symbolEl || !editorEl?.contains(symbolEl)) return;
    event.preventDefault();
    const token = String(symbolEl.getAttribute("data-token") || "");
    if (!token) return;
    dispatch("symbolclick", { token });
    editorEl.focus({ preventScroll: true });
  };

  const handleMouseMove = (event) => {
    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      const target = event.target instanceof Element ? event.target : null;
      const symbolEl = target?.closest(".vx-editor__symbol");
      if (symbolEl && editorEl?.contains(symbolEl)) {
        dispatch("hoverleave");
        return;
      }

      const tokenEl = target?.closest("[data-token-kind]");
      if (!tokenEl || !editorEl?.contains(tokenEl)) {
        dispatch("hoverleave");
        return;
      }

      const tokenKind = String(tokenEl.getAttribute("data-token-kind") || "");
      const tokenText = String(tokenEl.getAttribute("data-token-text") || "");
      if (tokenKind !== "operator" || !isOperatorToken(tokenText)) {
        dispatch("hoverleave");
        return;
      }

      dispatch("operatorhover", {
        token: tokenText,
        context: expressionContextAt(currentText(), Number(tokenEl.getAttribute("data-start") || 0)),
      });
    }, 100);
  };

  const handleMouseLeave = () => {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    dispatch("hoverleave");
    dispatch("symbolleave");
  };

  const handleBlur = () => {
    dispatch("hoverleave");
    dispatch("symbolleave");
  };

  const handleFocus = () => {};

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

  $: editorDocument = buildEditorDocument(
    editorText,
    symbols,
    diagnostics,
    symbolStatuses,
    selectedSymbols,
    symbolTypes,
  );

  $: editorText = String(value || "");

  $: if (!suggestionsOpen && suggestions.length) {
    suggestions = [];
  }

  afterUpdate(() => {
    if (!editorEl) return;
    if (pendingFocus) {
      editorEl.focus({ preventScroll: true });
    }
    if (pendingSelection) {
      restoreSelectionWithin(editorEl, currentText(), pendingSelection.start, pendingSelection.end);
    }
    if (pendingScroll) {
      editorEl.scrollLeft = pendingScroll.left;
      editorEl.scrollTop = pendingScroll.top;
    }
    pendingFocus = false;
    pendingSelection = null;
    pendingScroll = null;
  });

  onDestroy(() => {
    clearTimers();
  });
</script>

<div class="vx-editor" data-testid="vox-code-editor">
  <div class="vx-editor__shell">
    <div
      class="vx-editor__input vx-editor__surface"
      bind:this={editorEl}
      role="textbox"
      aria-multiline="true"
      aria-label={ariaLabel}
      contenteditable={readonly ? "false" : "true"}
      spellcheck="false"
      tabindex="0"
      data-empty={editorText.length ? "false" : "true"}
      data-placeholder={placeholder}
      data-readonly={readonly ? "true" : "false"}
      on:input={handleInput}
      on:keydown={handleKeydown}
      on:paste={handlePaste}
      on:mouseover={handleEditorMouseOver}
      on:mouseout={handleEditorMouseOut}
      on:mousedown={handleEditorMouseDown}
      on:click={handleEditorClick}
      on:mousemove={handleMouseMove}
      on:mouseleave={handleMouseLeave}
      on:focus={handleFocus}
      on:blur={handleBlur}
    >
      {#each editorDocument as line (`line-${line.number}`)}
        <div class={line.className} data-line={line.number} title={line.title || undefined}>
          {#if line.isEmpty}
            <br />
          {:else}
            {#each line.tokens as token (`token-${line.number}-${token.start}-${token.end}`)}
              <span
                class={token.className}
                data-start={token.start}
                data-end={token.end}
                data-token-kind={token.tokenKind}
                data-token-text={token.kind === "symbol" ? undefined : token.text}
                data-token={token.kind === "symbol" ? token.symbol : undefined}
                data-status={token.kind === "symbol" ? token.status : undefined}
                title={token.title || undefined}
              >{token.text}</span>
            {/each}
          {/if}
        </div>
      {/each}
    </div>
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

<svelte:window on:blur={() => clearSuggestionState(true)} on:beforeunload={clearTimers} />

<style>
  .vx-editor {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 0;
    display: grid;
  }

  .vx-editor__shell {
    position: relative;
    min-height: 0;
    height: 100%;
  }

  .vx-editor__surface {
    position: relative;
    width: 100%;
    min-height: 100%;
    height: 100%;
    overflow: auto;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: rgba(1, 10, 18, 0.8);
    color: #cce2ff;
    caret-color: #cce2ff;
    padding: 0.8rem;
    box-sizing: border-box;
    white-space: pre-wrap;
    word-break: normal;
    overflow-wrap: normal;
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.83rem;
    line-height: 1.5;
    tab-size: 2;
    font-variant-ligatures: none;
    font-feature-settings: "liga" 0, "calt" 0;
    transition: border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
  }

  .vx-editor__surface:focus {
    outline: 2px solid rgba(38, 198, 169, 0.35);
    outline-offset: 1px;
  }

  .vx-editor__surface[data-empty="true"]::before {
    content: attr(data-placeholder);
    position: absolute;
    inset: 0.8rem auto auto 0.8rem;
    color: #6f8498;
    pointer-events: none;
  }

  .vx-editor__surface[data-readonly="true"] {
    cursor: default;
  }

  .vx-editor__surface :global(::selection) {
    background: rgba(51, 105, 255, 0.36);
  }

  .vx-editor__line {
    display: block;
    min-height: 1.5em;
  }

  .vx-editor__line--error {
    background: rgba(240, 93, 94, 0.16);
    box-shadow: inset 3px 0 0 rgba(240, 93, 94, 0.75);
    cursor: help;
  }

  .vx-editor__token--space {
    white-space: pre-wrap;
  }

  .vx-editor__token--keyword {
    color: #8ef4df;
  }

  .vx-editor__token--number {
    color: #f7c26a;
  }

  .vx-editor__token--string {
    color: #c5f0a6;
  }

  .vx-editor__token--comment {
    color: #8ea6ba;
  }

  .vx-editor__token--operator {
    color: #b9cae2;
  }

  .vx-editor__token--identifier {
    color: #cce2ff;
  }

  .vx-editor__symbol {
    color: #8cecdc;
    cursor: pointer;
    text-decoration: none;
    transition: color 120ms ease;
  }

  .vx-editor__symbol:hover {
    color: #d8fff7;
  }

  .vx-editor__symbol--idle {
    color: #8cecdc;
  }

  .vx-editor__symbol--queued {
    color: #9cb0c6;
    text-decoration: underline dotted rgba(156, 176, 198, 0.8);
    text-underline-offset: 0.14em;
  }

  .vx-editor__symbol--running {
    color: #ffcc80;
    text-decoration: underline wavy rgba(255, 204, 128, 0.9);
    text-underline-offset: 0.15em;
  }

  .vx-editor__symbol--persisting {
    color: #8ed1ff;
    text-decoration: underline dashed rgba(142, 209, 255, 0.9);
    text-underline-offset: 0.14em;
  }

  .vx-editor__symbol--computed {
    color: #adffd0;
    text-shadow: 0 0 8px rgba(44, 215, 142, 0.35);
  }

  .vx-editor__symbol--failed {
    color: #ffb2b2;
    text-decoration: underline solid rgba(255, 120, 120, 0.95);
    text-underline-offset: 0.14em;
  }

  .vx-editor__symbol--selected {
    background: rgba(51, 105, 255, 0.22);
    border-radius: 4px;
    box-shadow: 0 0 0 1px rgba(51, 105, 255, 0.55);
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
