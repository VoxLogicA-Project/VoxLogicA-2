import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import VoxCodeEditor from "./VoxCodeEditor.svelte";
import VoxCodeEditorEventHarness from "./VoxCodeEditorEventHarness.svelte";

describe("VoxCodeEditor", () => {
  it("dispatches symbolclick when clicking linked symbol token", async () => {
    const handler = vi.fn();
    const { container } = render(VoxCodeEditorEventHarness, { onSymbolClick: handler });

    const symbolButton = container.querySelector(".vx-editor__symbol");
    expect(symbolButton).not.toBeNull();

    await fireEvent.click(symbolButton);
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

    const textarea = container.querySelector("textarea");
    expect(textarea).not.toBeNull();
    textarea.focus();
    textarea.setSelectionRange(2, 2);

    await fireEvent.keyDown(textarea, { key: " ", ctrlKey: true });

    await waitFor(() => {
      expect(screen.getByTestId("completion-list")).toBeInTheDocument();
    });

    await fireEvent.keyDown(textarea, { key: "Enter" });

    await waitFor(() => {
      expect(textarea.value).toBe("map");
    });
  });

  it("marks diagnostic lines in overlay", () => {
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
