import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import { readEditableText, restoreSelectionWithin } from "$lib/utils/vox-editor.js";
import VoxCodeEditor from "./VoxCodeEditor.svelte";
import VoxCodeEditorEventHarness from "./VoxCodeEditorEventHarness.svelte";

describe("VoxCodeEditor", () => {
  it("dispatches symbolclick when clicking linked symbol token", async () => {
    const handler = vi.fn();
    const { container } = render(VoxCodeEditorEventHarness, { onSymbolClick: handler });

    const symbolEl = container.querySelector(".vx-editor__symbol");
    expect(symbolEl).not.toBeNull();

    await fireEvent.click(symbolEl);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].token).toBe("x");
  });

  it("opens completion list with ctrl+space and applies with enter", async () => {
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

    const editor = container.querySelector(".vx-editor__surface");
    expect(editor).not.toBeNull();

    editor.focus();
    restoreSelectionWithin(editor, "ma", 2, 2);
    await fireEvent.keyDown(editor, { key: " ", ctrlKey: true });

    await waitFor(() => {
      expect(screen.getByTestId("completion-list")).toBeInTheDocument();
    });

    await fireEvent.keyDown(editor, { key: "Enter" });

    await waitFor(() => {
      expect(readEditableText(editor)).toBe("map");
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
  });
});
