import { describe, expect, it } from "vitest";

import {
  applyCompletion,
  buildDefaultCompletions,
  buildEditorDocument,
  completionContextAt,
  dragNumberToken,
  parseDiagnosticLocation,
  readEditableText,
  restoreSelectionWithin,
  selectionOffsetsWithin,
} from "./vox-editor.js";

describe("vox-editor utils", () => {
  it("parses line and column from diagnostics", () => {
    const loc = parseDiagnosticLocation({
      message: "Unexpected token at line 3, column 9.",
    });
    expect(loc).toEqual({ line: 3, column: 9 });
  });

  it("builds a render model with symbol metadata and diagnostic lines", () => {
    const documentModel = buildEditorDocument(
      "alpha = 1\nbeta = alpha",
      { alpha: "hash-alpha" },
      [{ message: "Unexpected token", location: "line 2, column 1" }],
      { alpha: "cached" },
      ["alpha"],
      { alpha: "integer" },
    );

    expect(documentModel).toHaveLength(2);
    expect(documentModel[1].className).toContain("vx-editor__line--error");

    const symbolToken = documentModel[0].tokens.find((token) => token.kind === "symbol");
    expect(symbolToken).toMatchObject({
      symbol: "alpha",
      status: "computed",
      selected: true,
      title: "alpha (integer)",
    });
  });

  it("reads editable DOM as plain text and restores selection offsets", () => {
    const root = document.createElement("div");
    const lineOne = document.createElement("div");
    lineOne.setAttribute("data-line", "1");
    const lineOneText = document.createElement("span");
    lineOneText.textContent = "alpha";
    lineOne.appendChild(lineOneText);

    const lineTwo = document.createElement("div");
    lineTwo.setAttribute("data-line", "2");
    lineTwo.appendChild(document.createElement("br"));

    const lineThree = document.createElement("div");
    lineThree.setAttribute("data-line", "3");
    const lineThreeText = document.createElement("span");
    lineThreeText.textContent = "beta";
    lineThree.appendChild(lineThreeText);

    root.append(lineOne, lineTwo, lineThree);
    document.body.appendChild(root);

    try {
      expect(readEditableText(root)).toBe("alpha\n\nbeta");
      expect(restoreSelectionWithin(root, "alpha\n\nbeta", 7, 7)).toBe(true);
      expect(selectionOffsetsWithin(root)).toMatchObject({
        start: 7,
        end: 7,
        collapsed: true,
      });
    } finally {
      root.remove();
    }
  });

  it("computes completion context and applies selected completion", () => {
    const source = "ma";
    const ctx = completionContextAt(source, 2);
    expect(ctx.prefix).toBe("ma");

    const completions = buildDefaultCompletions(ctx, {
      symbols: { mask: "hash" },
      keywords: ["map", "let"],
      builtins: [],
    });
    expect(completions.map((row) => row.label)).toContain("map");
    expect(completions.map((row) => row.label)).toContain("mask");

    const applied = applyCompletion(source, ctx, { label: "map", insertText: "map" });
    expect(applied.text).toBe("map");
    expect(applied.cursor).toBe(3);
  });

  it("adjusts numeric tokens with vertical drag and horizontal granularity", () => {
    expect(dragNumberToken("5", { deltaX: 0, deltaY: -12 })).toMatchObject({
      text: "7",
      value: 7,
      step: 1,
      steps: 2,
      granularityLevel: 0,
    });

    expect(dragNumberToken("5", { deltaX: -80, deltaY: -6 })).toMatchObject({
      text: "5.1",
      value: 5.1,
      step: 0.1,
      steps: 1,
      granularityLevel: -1,
    });

    expect(dragNumberToken("5", { deltaX: 80, deltaY: -6 })).toMatchObject({
      text: "15",
      value: 15,
      step: 10,
      steps: 1,
      granularityLevel: 1,
    });

    expect(dragNumberToken("1.25", { deltaX: 80, deltaY: 6 })).toMatchObject({
      text: "1.15",
      value: 1.15,
      step: 0.1,
      steps: -1,
      granularityLevel: 1,
    });
  });
});
