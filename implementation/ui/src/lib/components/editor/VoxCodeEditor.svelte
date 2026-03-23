<script>
  import { afterUpdate, createEventDispatcher, onDestroy } from "svelte";
  import {
    applyCompletion,
    buildDefaultCompletions,
    buildEditorDocument,
    completionContextAt,
    dragNumberToken,
    expressionContextAt,
    extractTokenInfoAt,
    isOperatorToken,
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

  let textareaEl;
  let overlayContentEl;
  let hoverTimer = null;
  let completionTimer = null;
  let completionRequestToken = 0;
  let destroyed = false;
  let editorDocument = [];
  let editorText = "";
  let overlayScrollLeft = 0;
  let overlayScrollTop = 0;
  let pendingSelection = null;
  let pendingScroll = null;
  let pendingFocus = false;
  let editorFocused = false;
  let surfaceCursor = "text";
  let activeNumberDrag = null;
  let suppressNextClick = false;

  let suggestions = [];
  let selectedSuggestion = 0;
  let suggestionsOpen = false;
  let completionContext = null;
  let suggestionInteractionMode = "passive";
  let suggestionsPos = { left: 0, top: 0 };

  const NUMBER_DRAG_ACTIVATION_PX = 4;

  const diagnosticLocationLabel = (diagnostic) => {
    const location = String(diagnostic?.location || "").trim();
    if (!location) return "";
    const match = location.match(/line\s+(\d+)(?:\s*,\s*column\s+(\d+))?/i);
    if (!match) return location;
    return `Line ${match[1]}${match[2] ? `:${match[2]}` : ""}`;
  };

  const diagnosticItems = () =>
    (Array.isArray(diagnostics) ? diagnostics : []).map((diagnostic) => ({
      message: String(diagnostic?.message || "Static error").trim(),
      locationLabel: diagnosticLocationLabel(diagnostic),
      code: String(diagnostic?.code || "").trim(),
    }));

  const diagnosticsSummary = () => {
    const items = diagnosticItems();
    if (!items.length) return "";
    const first = items[0];
    const summary = [first.locationLabel, first.message].filter(Boolean).join(" - ");
    if (items.length === 1) return summary;
    return `${summary} (+${items.length - 1} more)`;
  };

  const currentText = () => editorText;

  const currentSelection = () => {
    if (!textareaEl) {
      const length = currentText().length;
      return {
        anchor: length,
        focus: length,
        start: length,
        end: length,
        collapsed: true,
      };
    }

    const start = Number(textareaEl.selectionStart || 0);
    const end = Number(textareaEl.selectionEnd || 0);
    return {
      anchor: start,
      focus: end,
      start,
      end,
      collapsed: start === end,
    };
  };

  const currentViewState = () => ({
    selectionStart: Number(currentSelection().start || 0),
    selectionEnd: Number(currentSelection().end || 0),
    scrollTop: Math.max(0, Number(textareaEl?.scrollTop || 0)),
    scrollLeft: Math.max(0, Number(textareaEl?.scrollLeft || 0)),
  });

  const emitViewState = () => {
    dispatch("viewstate", currentViewState());
  };

  const preserveViewport = () => {
    if (!textareaEl) return;
    pendingScroll = {
      left: textareaEl.scrollLeft,
      top: textareaEl.scrollTop,
    };
  };

  const queueSelectionRestore = (start, end = start, { focus = false } = {}) => {
    pendingSelection = { start, end };
    pendingFocus = pendingFocus || focus;
    preserveViewport();
  };

  const syncOverlayScroll = () => {
    overlayScrollLeft = Number(textareaEl?.scrollLeft || 0);
    overlayScrollTop = Number(textareaEl?.scrollTop || 0);
  };

  const emitTextChange = (nextValue, emitInput = true) => {
    dispatch("change", { value: nextValue });
    if (emitInput) {
      dispatch("input", { value: nextValue });
    }
  };

  const clearSuggestionState = (notify = false) => {
    const wasOpen = suggestionsOpen;
    suggestions = [];
    selectedSuggestion = 0;
    suggestionsOpen = false;
    completionContext = null;
    suggestionInteractionMode = "passive";
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
      if (destroyed) return;
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

  const measureCaretRect = () => {
    if (!textareaEl) return null;
    if (typeof document === "undefined") return null;
    if (typeof navigator !== "undefined" && /jsdom/i.test(String(navigator.userAgent || ""))) {
      return textareaEl.getBoundingClientRect();
    }

    const text = currentText();
    const cursor = Number(textareaEl.selectionEnd || 0);
    const style = getComputedStyle(textareaEl);
    const rect = textareaEl.getBoundingClientRect();
    const lineHeight = Number.parseFloat(style.lineHeight || "0") || (Number.parseFloat(style.fontSize || "0") * 1.5);
    const paddingLeft = Number.parseFloat(style.paddingLeft || "0") || 0;
    const paddingTop = Number.parseFloat(style.paddingTop || "0") || 0;
    const lineStart = text.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
    const lineText = text.slice(lineStart, cursor).replaceAll("\t", "  ");
    const lineIndex = text.slice(0, cursor).split("\n").length - 1;
    const canvas = measureCaretRect.canvas || (measureCaretRect.canvas = document.createElement("canvas"));
    const context = typeof canvas.getContext === "function" ? canvas.getContext("2d") : null;
    if (!context) return rect;

    context.font = style.font || `${style.fontSize} ${style.fontFamily}`;
    const width = context.measureText(lineText).width;
    const left = rect.left + paddingLeft + width - textareaEl.scrollLeft;
    const top = rect.top + paddingTop + (lineIndex * lineHeight) - textareaEl.scrollTop;

    return {
      left,
      top,
      bottom: top + lineHeight,
      height: lineHeight,
    };
  };
  measureCaretRect.canvas = null;

  const openCompletions = async (forced) => {
    if (destroyed || !autocompleteEnabled || readonly || !textareaEl) return;
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

    if (destroyed || requestToken !== completionRequestToken) return;

    const normalized = toCompletionItems(items, context);
    if (!normalized.length) {
      clearSuggestionState(true);
      return;
    }

    const caret = measureCaretRect() || textareaEl.getBoundingClientRect();
    suggestions = normalized;
    selectedSuggestion = 0;
    suggestionsOpen = true;
    completionContext = context;
    suggestionInteractionMode = forced || suggestionInteractionMode === "manual" ? "manual" : "passive";
    suggestionsPos = {
      left: Math.max(8, Math.round((caret.left || 0) + 8)),
      top: Math.max(8, Math.round((caret.bottom || caret.top || 0) + 6)),
    };
    dispatch("completionstate", {
      open: true,
      count: suggestions.length,
      prefix: context.prefix,
    });
  };

  const updateModelFromTextarea = (emitInput = true) => {
    if (!textareaEl) return;
    syncOverlayScroll();
    const nextText = textareaEl.value;
    if (nextText !== currentText()) {
      value = nextText;
      emitTextChange(nextText, emitInput);
    }
  };

  const replaceTextRange = (start, end, insertText, { emitInput = true, focus = true, selectionMode = "end" } = {}) => {
    if (!textareaEl) {
      const source = currentText();
      const safeStart = Math.max(0, Math.min(Number(start || 0), source.length));
      const safeEnd = Math.max(safeStart, Math.min(Number(end || 0), source.length));
      const nextText = `${source.slice(0, safeStart)}${insertText}${source.slice(safeEnd)}`;
      const nextCursor = safeStart + insertText.length;
      value = nextText;
      emitTextChange(nextText, emitInput);
      queueSelectionRestore(nextCursor, nextCursor, { focus });
      return;
    }

    const source = textareaEl.value;
    const safeStart = Math.max(0, Math.min(Number(start || 0), source.length));
    const safeEnd = Math.max(safeStart, Math.min(Number(end || 0), source.length));
    const nextText = `${source.slice(0, safeStart)}${insertText}${source.slice(safeEnd)}`;
    const insertedEnd = safeStart + insertText.length;
    let nextSelectionStart = insertedEnd;
    let nextSelectionEnd = insertedEnd;

    if (selectionMode === "select") {
      nextSelectionStart = safeStart;
      nextSelectionEnd = insertedEnd;
    } else if (selectionMode === "start") {
      nextSelectionStart = safeStart;
      nextSelectionEnd = safeStart;
    } else if (selectionMode === "preserve") {
      const delta = insertText.length - (safeEnd - safeStart);
      nextSelectionStart = safeStart;
      nextSelectionEnd = Math.max(safeStart, safeEnd + delta);
    }

    if (focus) {
      textareaEl.focus({ preventScroll: true });
    }
    preserveViewport();

    if (typeof textareaEl.setRangeText === "function") {
      try {
        textareaEl.setSelectionRange(safeStart, safeEnd, "none");
        textareaEl.setRangeText(insertText, safeStart, safeEnd, selectionMode);
      } catch {
        textareaEl.value = nextText;
      }
    }

    if (textareaEl.value !== nextText) {
      textareaEl.value = nextText;
    }

    textareaEl.setSelectionRange(nextSelectionStart, nextSelectionEnd, "none");
    updateModelFromTextarea(emitInput);
  };

  const replaceSelection = (insertText, options = {}) => {
    const selection = currentSelection();
    replaceTextRange(selection.start, selection.end, insertText, options);
  };

  const applyActiveSuggestion = async () => {
    if (!suggestionsOpen || !suggestions.length || !completionContext) return false;
    const item = suggestions[Math.max(0, Math.min(selectedSuggestion, suggestions.length - 1))];
    const context = completionContext;
    const applied = applyCompletion(currentText(), context, item);
    clearSuggestionState(true);
    replaceTextRange(context.from, context.to, item.insertText, {
      emitInput: false,
      focus: true,
      selectionMode: "end",
    });
    if (textareaEl) {
      textareaEl.setSelectionRange(applied.cursor, applied.cursor, "none");
    }
    dispatch("completionapply", { item, cursor: applied.cursor });
    return true;
  };

  const tokenInfoNearSelection = () => {
    const selection = currentSelection();
    if (!selection.collapsed) return null;

    const cursor = selection.start;
    let token = extractTokenInfoAt(currentText(), cursor);
    if (!token.token && cursor > 0) {
      token = extractTokenInfoAt(currentText(), cursor - 1);
    }
    if (!token.token) return null;
    return token;
  };

  const tokenElementFromPoint = (clientX, clientY) => {
    if (!overlayContentEl) return null;

    const tokenNodes = Array.from(overlayContentEl.querySelectorAll("[data-token-kind]"));
    for (const tokenEl of tokenNodes) {
      const rect = tokenEl.getBoundingClientRect();
      if (
        clientX >= rect.left &&
        clientX <= rect.right &&
        clientY >= rect.top &&
        clientY <= rect.bottom
      ) {
        return tokenEl;
      }
    }

    const ownerDocument = overlayContentEl.ownerDocument;
    const tokenFromNode = (node) => {
      const element = node?.nodeType === 1 ? node : node?.parentElement;
      const tokenEl = element?.closest?.("[data-token-kind]");
      return tokenEl && overlayContentEl.contains(tokenEl) ? tokenEl : null;
    };

    if (typeof ownerDocument?.caretPositionFromPoint === "function") {
      const caret = ownerDocument.caretPositionFromPoint(clientX, clientY);
      const tokenEl = tokenFromNode(caret?.offsetNode || null);
      if (tokenEl) return tokenEl;
    }

    if (typeof ownerDocument?.caretRangeFromPoint === "function") {
      const range = ownerDocument.caretRangeFromPoint(clientX, clientY);
      const tokenEl = tokenFromNode(range?.startContainer || null);
      if (tokenEl) return tokenEl;
    }

    return null;
  };

  const handleInput = () => {
    updateModelFromTextarea(true);
    if (!activeNumberDrag?.active) {
      scheduleCompletion();
    }
    emitViewState();
  };

  const handleScroll = () => {
    syncOverlayScroll();
    emitViewState();
  };

  const handleSelect = () => {
    if (activeNumberDrag?.active) return;
    if (suggestionsOpen) {
      scheduleCompletion();
    }
    emitViewState();
  };

  const endNumberDrag = () => {
    if (!activeNumberDrag) return;

    const drag = activeNumberDrag;
    activeNumberDrag = null;

    if (drag.active) {
      queueSelectionRestore(drag.start, drag.end, { focus: true });
    }

    if (textareaEl && typeof textareaEl.releasePointerCapture === "function" && drag.pointerId != null) {
      try {
        textareaEl.releasePointerCapture(drag.pointerId);
      } catch {
        // Pointer capture is not guaranteed in jsdom or every browser path.
      }
    }
  };

  const handlePointerDown = (event) => {
    if (readonly || event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey) return;

    const tokenEl = tokenElementFromPoint(event.clientX, event.clientY);
    if (String(tokenEl?.getAttribute?.("data-token-kind") || "") !== "number") return;

    const tokenText = String(tokenEl.getAttribute("data-token-text") || "");
    if (!dragNumberToken(tokenText)) return;

    clearSuggestionState(true);
    suppressNextClick = false;
    activeNumberDrag = {
      pointerId: event.pointerId,
      originX: event.clientX,
      originY: event.clientY,
      start: Number(tokenEl.getAttribute("data-start") || 0),
      end: Number(tokenEl.getAttribute("data-end") || 0),
      tokenText,
      lastText: tokenText,
      active: false,
    };
  };

  const handlePointerMove = (event) => {
    const drag = activeNumberDrag;
    if (!drag || event.pointerId !== drag.pointerId) return;

    const deltaX = event.clientX - drag.originX;
    const deltaY = event.clientY - drag.originY;
    const distance = Math.max(Math.abs(deltaX), Math.abs(deltaY));

    if (!drag.active && distance < NUMBER_DRAG_ACTIVATION_PX) {
      return;
    }

    if (!drag.active) {
      drag.active = true;
      suppressNextClick = true;
      surfaceCursor = "ns-resize";
      if (textareaEl) {
        textareaEl.focus({ preventScroll: true });
        if (typeof textareaEl.setPointerCapture === "function") {
          try {
            textareaEl.setPointerCapture(event.pointerId);
          } catch {
            // Pointer capture is not guaranteed in jsdom or every browser path.
          }
        }
      }
    }

    const adjustment = dragNumberToken(drag.tokenText, { deltaX, deltaY });
    if (!adjustment) return;

    surfaceCursor = "ns-resize";
    if (adjustment.text === drag.lastText) {
      event.preventDefault();
      return;
    }

    replaceTextRange(drag.start, drag.end, adjustment.text, {
      emitInput: true,
      focus: true,
      selectionMode: "select",
    });
    drag.end = drag.start + adjustment.text.length;
    drag.lastText = adjustment.text;
    event.preventDefault();
  };

  const handlePointerUp = (event) => {
    if (!activeNumberDrag || event.pointerId !== activeNumberDrag.pointerId) return;
    event.preventDefault();
    endNumberDrag();
  };

  const handlePointerCancel = (event) => {
    if (!activeNumberDrag || event.pointerId !== activeNumberDrag.pointerId) return;
    endNumberDrag();
  };

  const handleKeydown = async (event) => {
    if ((event.ctrlKey || event.metaKey) && String(event.key || "").toLowerCase() === " ") {
      event.preventDefault();
      await openCompletions(true);
      return;
    }

    if (suggestionsOpen) {
      if (event.key === "Escape") {
        event.preventDefault();
        clearSuggestionState(true);
        return;
      }

      if (suggestionInteractionMode !== "manual") {
        if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Enter"].includes(event.key)) {
          clearSuggestionState(true);
        }
      }

      if (suggestionInteractionMode === "manual") {
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
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        await applyActiveSuggestion();
        return;
      }
      }
    }

    if (!readonly && event.key === "Tab" && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      replaceSelection("  ", { emitInput: true, focus: true, selectionMode: "end" });
      emitViewState();
    }
  };

  const handleTextareaClick = (event) => {
    if (suppressNextClick) {
      suppressNextClick = false;
      event.preventDefault();
      return;
    }

    const pointToken = tokenElementFromPoint(event.clientX, event.clientY);
    const pointSymbol = String(pointToken?.getAttribute?.("data-token") || "");
    if (pointSymbol) {
      dispatch("symbolclick", { token: pointSymbol });
      return;
    }

    const token = tokenInfoNearSelection();
    if (token?.token && symbols?.[token.token]) {
      dispatch("symbolclick", { token: token.token });
    }
  };

  const handleMouseMove = (event) => {
    const tokenEl = tokenElementFromPoint(event.clientX, event.clientY);
    const symbolToken = String(tokenEl?.getAttribute("data-token") || "");
    const tokenKind = String(tokenEl?.getAttribute("data-token-kind") || "");
    const tokenText = String(tokenEl?.getAttribute("data-token-text") || "");

    if (symbolToken) {
      surfaceCursor = "pointer";
    } else if (tokenKind === "number" && !readonly) {
      surfaceCursor = "ns-resize";
    } else if (tokenKind === "operator" && isOperatorToken(tokenText)) {
      surfaceCursor = "help";
    } else {
      surfaceCursor = readonly ? "default" : "text";
    }

    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      if (destroyed) return;
      const tokenEl = tokenElementFromPoint(event.clientX, event.clientY);
      if (!tokenEl) {
        dispatch("hoverleave");
        dispatch("symbolleave");
        return;
      }

      const symbolToken = String(tokenEl.getAttribute("data-token") || "");
      if (symbolToken) {
        dispatch("symbolhover", { token: symbolToken });
        dispatch("hoverleave");
        return;
      }

      dispatch("symbolleave");
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
    if (activeNumberDrag?.active) return;
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    surfaceCursor = readonly ? "default" : "text";
    dispatch("hoverleave");
    dispatch("symbolleave");
  };

  const handleBlur = () => {
    editorFocused = false;
    endNumberDrag();
    surfaceCursor = readonly ? "default" : "text";
    dispatch("hoverleave");
    dispatch("symbolleave");
    emitViewState();
  };

  const handleFocus = () => {
    editorFocused = true;
    emitViewState();
  };

  export function getViewState() {
    return currentViewState();
  }

  export function restoreViewState(state = {}) {
    const start = Math.max(0, Number(state?.selectionStart || 0));
    const end = Math.max(start, Number(state?.selectionEnd || state?.selectionStart || 0));
    const scrollState = {
      left: Math.max(0, Number(state?.scrollLeft || 0)),
      top: Math.max(0, Number(state?.scrollTop || 0)),
    };
    if (textareaEl) {
      textareaEl.setSelectionRange(start, end, "none");
      textareaEl.scrollLeft = scrollState.left;
      textareaEl.scrollTop = scrollState.top;
      syncOverlayScroll();
      emitViewState();
      return;
    }
    pendingSelection = { start, end };
    pendingScroll = scrollState;
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
    if (!textareaEl) return;
    if (textareaEl.value !== editorText) {
      textareaEl.value = editorText;
    }
    if (pendingFocus) {
      textareaEl.focus({ preventScroll: true });
    }
    if (pendingSelection) {
      textareaEl.setSelectionRange(pendingSelection.start, pendingSelection.end, "none");
    }
    if (pendingScroll) {
      textareaEl.scrollLeft = pendingScroll.left;
      textareaEl.scrollTop = pendingScroll.top;
    }
    syncOverlayScroll();
    pendingFocus = false;
    pendingSelection = null;
    pendingScroll = null;
    emitViewState();
  });

  onDestroy(() => {
    destroyed = true;
    endNumberDrag();
    clearTimers();
  });
</script>

<div class="vx-editor" data-testid="vox-code-editor">
  <div class="vx-editor__shell">
    <div
      class="vx-editor__surface"
      data-empty={editorText.length ? "false" : "true"}
      data-focused={editorFocused ? "true" : "false"}
      data-has-diagnostics={diagnosticItems().length ? "true" : "false"}
      data-cursor={surfaceCursor}
      data-placeholder={placeholder}
      data-readonly={readonly ? "true" : "false"}
    >
      <div class="vx-editor__overlay" aria-hidden="true">
        <div
          class="vx-editor__overlay-content"
          bind:this={overlayContentEl}
          style={`transform: translate(${-overlayScrollLeft}px, ${-overlayScrollTop}px)`}
        >
          {#each editorDocument as line (`line-${line.number}`)}
            <div class={line.className} data-line={line.number} title={line.title || undefined}>
              {#if line.isEmpty}
                <span class="vx-editor__line-placeholder"> </span>
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

      <textarea
        class="vx-editor__textarea"
        bind:this={textareaEl}
        aria-label={ariaLabel}
        spellcheck="false"
        tabindex="0"
        readonly={readonly}
        wrap="off"
        value={editorText}
        on:input={handleInput}
        on:keydown={handleKeydown}
        on:scroll={handleScroll}
        on:select={handleSelect}
        on:click={handleTextareaClick}
        on:pointerdown={handlePointerDown}
        on:pointermove={handlePointerMove}
        on:pointerup={handlePointerUp}
        on:pointercancel={handlePointerCancel}
        on:mousemove={handleMouseMove}
        on:mouseleave={handleMouseLeave}
        on:focus={handleFocus}
        on:blur={handleBlur}
      ></textarea>
    </div>

    {#if diagnosticItems().length}
      <div class="vx-editor__diagnostics" role="status" aria-live="polite">
        <span class="vx-editor__diagnostics-label">Editor</span>
        <span class="vx-editor__diagnostics-text">{diagnosticsSummary()}</span>
      </div>
    {/if}
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
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: rgba(1, 10, 18, 0.8);
    transition: border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
  }

  .vx-editor__surface:focus-within {
    outline: 2px solid rgba(38, 198, 169, 0.35);
    outline-offset: 1px;
  }

  .vx-editor__surface[data-empty="true"]::before {
    content: attr(data-placeholder);
    position: absolute;
    inset: 0.8rem auto auto 0.8rem;
    color: #6f8498;
    pointer-events: none;
    z-index: 0;
  }

  .vx-editor__surface[data-readonly="true"] {
    cursor: default;
  }

  .vx-editor__surface[data-has-diagnostics="true"] .vx-editor__overlay,
  .vx-editor__surface[data-has-diagnostics="true"] .vx-editor__textarea {
    padding-bottom: 2.5rem;
  }

  .vx-editor__surface[data-cursor="pointer"] .vx-editor__textarea {
    cursor: pointer;
  }

  .vx-editor__surface[data-cursor="help"] .vx-editor__textarea {
    cursor: help;
  }

  .vx-editor__surface[data-cursor="ns-resize"] .vx-editor__textarea {
    cursor: ns-resize;
  }

  .vx-editor__overlay,
  .vx-editor__textarea {
    position: absolute;
    inset: 0;
    box-sizing: border-box;
    padding: 0.8rem;
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.83rem;
    line-height: 1.5;
    tab-size: 2;
    letter-spacing: normal;
    font-variant-ligatures: none;
    font-feature-settings: "liga" 0, "calt" 0;
  }

  .vx-editor__overlay {
    pointer-events: none;
    overflow: hidden;
    color: #cce2ff;
    white-space: pre;
    z-index: 1;
  }

  .vx-editor__surface[data-focused="true"] .vx-editor__overlay {
    opacity: 0;
  }

  .vx-editor__overlay-content {
    min-width: 100%;
    width: max-content;
    will-change: transform;
  }

  .vx-editor__textarea {
    margin: 0;
    border: none;
    resize: none;
    background: transparent;
    color: transparent;
    caret-color: #cce2ff;
    -webkit-text-fill-color: transparent;
    overflow: auto;
    white-space: pre;
    word-break: normal;
    overflow-wrap: normal;
    outline: none;
    z-index: 2;
  }

  .vx-editor__surface[data-focused="true"] .vx-editor__textarea {
    color: #cce2ff;
    -webkit-text-fill-color: #cce2ff;
  }

  .vx-editor__textarea::selection {
    background: rgba(51, 105, 255, 0.36);
  }

  .vx-editor__line {
    display: block;
    min-height: 1.5em;
    letter-spacing: normal;
    white-space: pre;
  }

  .vx-editor__token,
  .vx-editor__symbol {
    letter-spacing: normal;
  }

  .vx-editor__line-placeholder {
    display: inline-block;
    min-width: 1px;
  }

  .vx-editor__line--error {
    background: rgba(240, 93, 94, 0.16);
    box-shadow: inset 3px 0 0 rgba(240, 93, 94, 0.75);
    cursor: help;
  }

  .vx-editor__diagnostics {
    position: absolute;
    left: 0.6rem;
    right: 0.6rem;
    bottom: 0.55rem;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 0.42rem;
    min-width: 0;
    border: 1px solid rgba(219, 116, 99, 0.34);
    border-radius: 999px;
    background: rgba(255, 247, 244, 0.94);
    box-shadow: 0 8px 18px rgba(99, 50, 31, 0.08);
    padding: 0.34rem 0.55rem;
    color: #7a3b33;
  }

  .vx-editor__diagnostics-label {
    flex: 0 0 auto;
    font-size: 0.64rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #a14b40;
  }

  .vx-editor__diagnostics-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.72rem;
    line-height: 1.2;
  }

  .vx-editor__token--space {
    white-space: pre;
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
    text-decoration: none;
    transition: color 120ms ease;
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
