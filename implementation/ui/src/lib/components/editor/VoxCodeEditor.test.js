import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import VoxCodeEditor from "./VoxCodeEditor.svelte";
import VoxCodeEditorEventHarness from "./VoxCodeEditorEventHarness.svelte";

const dispatchCancelableKeydown = (element, init) => {
  const event = new KeyboardEvent("keydown", {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  element.dispatchEvent(event);
  return event;
};

afterEach(() => {
  cleanup();
});

describe("VoxCodeEditor", () => {
  it("dispatches symbolclick when clicking linked symbol token", async () => {
    const handler = vi.fn();
    const { container } = render(VoxCodeEditorEventHarness, { onSymbolClick: handler });

    const textarea = container.querySelector(".vx-editor__textarea");
    expect(textarea).not.toBeNull();

    textarea.focus();
    textarea.setSelectionRange(0, 0);
    await fireEvent.click(textarea);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].token).toBe("x");
  });

  it("opens completion list with ctrl+space", async () => {
    const provider = vi.fn().mockResolvedValue([
      { label: "map", insertText: "map", kind: "primitive" },
      { label: "mask", insertText: "mask", kind: "symbol" },
    ]);

    const { container } = render(VoxCodeEditor, {
      value: "ma",
      symbols: {},
      diagnostics: [],
      completionProvider: provider,
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(2, 2);
    await fireEvent.keyDown(editor, { key: " ", ctrlKey: true });

    await waitFor(() => {
      expect(within(container).getByTestId("completion-list")).toBeInTheDocument();
    });
  });

  it("does not trap ArrowDown for passive autocomplete", async () => {
    const provider = vi.fn().mockResolvedValue([
      { label: "alpha", insertText: "alpha", kind: "symbol" },
      { label: "apply", insertText: "apply", kind: "keyword" },
    ]);

    const { container } = render(VoxCodeEditor, {
      value: "ap",
      symbols: {},
      diagnostics: [],
      completionProvider: provider,
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(2, 2);
    editor.value = "app";
    await fireEvent.input(editor);

    await waitFor(() => {
      expect(within(container).getByTestId("completion-list")).toBeInTheDocument();
    });

    const event = dispatchCancelableKeydown(editor, { key: "ArrowDown" });
    expect(event.defaultPrevented).toBe(false);

    await waitFor(() => {
      expect(within(container).queryByTestId("completion-list")).not.toBeInTheDocument();
    });
  });

  it("uses ArrowDown to navigate when autocomplete was explicitly opened", async () => {
    const provider = vi.fn().mockResolvedValue([
      { label: "map", insertText: "map", kind: "primitive" },
      { label: "mask", insertText: "mask", kind: "symbol" },
    ]);

    const { container } = render(VoxCodeEditor, {
      value: "ma",
      symbols: {},
      diagnostics: [],
      completionProvider: provider,
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(2, 2);
    await fireEvent.keyDown(editor, { key: " ", ctrlKey: true });

    await waitFor(() => {
      expect(within(container).getByTestId("completion-list")).toBeInTheDocument();
    });

    const event = dispatchCancelableKeydown(editor, { key: "ArrowDown" });
    expect(event.defaultPrevented).toBe(true);

    await waitFor(() => {
      const options = within(container).getAllByRole("option");
      expect(options[1]).toHaveAttribute("aria-selected", "true");
    });
  });

  it("marks diagnostic lines in the editor surface", () => {
    const { container } = render(VoxCodeEditor, {
      value: "a = 1\nb = 2",
      symbols: {},
      diagnostics: [
        {
          code: "E_PARSE",
          message: "Unexpected token",
          location: "line 2, column 1",
        },
      ],
    });

    const marked = container.querySelector('.vx-editor__line[data-line="2"]');
    expect(marked).not.toBeNull();
    expect(marked).toHaveClass("vx-editor__line--error");
    expect(container.querySelector(".vx-editor__diagnostics")?.textContent || "").toContain("Line 2:1 - Unexpected token");
  });

  it("applies native textarea input at the correct cursor location", async () => {
    const { container } = render(VoxCodeEditor, {
      value: "alpha\nbeta",
      symbols: { alpha: "hash-alpha", beta: "hash-beta" },
      diagnostics: [],
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(6, 6);
    editor.value = "alpha\nxbeta";
    await fireEvent.input(editor);

    await waitFor(() => {
      expect(editor.value).toBe("alpha\nxbeta");
    });
  });

  it("handles native newline edits without shifting the rendered lines", async () => {
    const { container } = render(VoxCodeEditor, {
      value: "alpha",
      symbols: {},
      diagnostics: [],
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(5, 5);
    editor.value = "alpha\n";
    await fireEvent.input(editor);

    await waitFor(() => {
      const lines = container.querySelectorAll(".vx-editor__line");
      expect(lines).toHaveLength(2);
      expect(lines[1].querySelector(".vx-editor__line-placeholder")).not.toBeNull();
    });
  });

  it("inserts indentation with tab at the current selection", async () => {
    const { container } = render(VoxCodeEditor, {
      value: "alpha",
      symbols: {},
      diagnostics: [],
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(5, 5);
    await fireEvent.keyDown(editor, { key: "Tab" });

    await waitFor(() => {
      expect(editor.value).toBe("alpha  ");
      expect(editor.selectionStart).toBe(7);
      expect(editor.selectionEnd).toBe(7);
    });
  });

  it("rewrites a numeric token while dragging upward with horizontal granularity", async () => {
    const { container } = render(VoxCodeEditor, {
      value: "threshold = 5",
      symbols: {},
      diagnostics: [],
    });

    const editor = container.querySelector(".vx-editor__textarea");
    const numberToken = container.querySelector('.vx-editor__token--number[data-token-text="5"]');
    expect(editor).not.toBeNull();
    expect(numberToken).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(12, 13);
    numberToken.getBoundingClientRect = () => ({
      left: 10,
      right: 30,
      top: 10,
      bottom: 30,
      width: 20,
      height: 20,
      x: 10,
      y: 10,
      toJSON: () => ({}),
    });

    await fireEvent.pointerDown(editor, {
      pointerId: 1,
      button: 0,
      clientX: 16,
      clientY: 24,
    });
    await fireEvent.pointerMove(editor, {
      pointerId: 1,
      clientX: 16,
      clientY: 12,
    });
    await fireEvent.pointerUp(editor, {
      pointerId: 1,
      clientX: 16,
      clientY: 12,
    });

    await waitFor(() => {
      expect(editor.value).toBe("threshold = 7");
      expect(editor.selectionStart).toBe(12);
      expect(editor.selectionEnd).toBe(13);
    });
  });

  it("falls back to caret-based token lookup when rectangle hit-testing misses", async () => {
    const { container } = render(VoxCodeEditor, {
      value: "threshold = 5",
      symbols: {},
      diagnostics: [],
    });

    const editor = container.querySelector(".vx-editor__textarea");
    const numberToken = container.querySelector('.vx-editor__token--number[data-token-text="5"]');
    expect(editor).not.toBeNull();
    expect(numberToken).not.toBeNull();

    editor.focus();
    editor.setSelectionRange(12, 13);

    numberToken.getBoundingClientRect = () => ({
      left: 0,
      right: 0,
      top: 0,
      bottom: 0,
      width: 0,
      height: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    const ownerDocument = numberToken.ownerDocument;
    const originalCaretPositionFromPoint = ownerDocument.caretPositionFromPoint;
    const originalCaretRangeFromPoint = ownerDocument.caretRangeFromPoint;

    ownerDocument.caretPositionFromPoint = vi.fn(() => ({
      offsetNode: numberToken.firstChild,
      offset: 0,
    }));
    ownerDocument.caretRangeFromPoint = undefined;

    try {
      await fireEvent.pointerDown(editor, {
        pointerId: 2,
        button: 0,
        clientX: 16,
        clientY: 24,
      });
      await fireEvent.pointerMove(editor, {
        pointerId: 2,
        clientX: 16,
        clientY: 12,
      });
      await fireEvent.pointerUp(editor, {
        pointerId: 2,
        clientX: 16,
        clientY: 12,
      });

      await waitFor(() => {
        expect(editor.value).toBe("threshold = 7");
      });
    } finally {
      ownerDocument.caretPositionFromPoint = originalCaretPositionFromPoint;
      ownerDocument.caretRangeFromPoint = originalCaretRangeFromPoint;
    }
  });

  it("prevents native text dragging so numeric drag owns pointer gestures", () => {
    const { container } = render(VoxCodeEditor, {
      value: "threshold = 5",
      symbols: {},
      diagnostics: [],
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    const dragEvent = new Event("dragstart", {
      bubbles: true,
      cancelable: true,
    });

    editor.dispatchEvent(dragEvent);
    expect(dragEvent.defaultPrevented).toBe(true);
    expect(editor.getAttribute("draggable")).toBe("false");
  });
});
