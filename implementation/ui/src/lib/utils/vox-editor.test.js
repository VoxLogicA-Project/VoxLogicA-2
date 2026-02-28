import { describe, expect, it } from "vitest";

import {
  applyCompletion,
  buildDefaultCompletions,
  completionContextAt,
  parseDiagnosticLocation,
} from "./vox-editor.js";

describe("vox-editor utils", () => {
  it("parses line and column from diagnostics", () => {
    const loc = parseDiagnosticLocation({
      message: "Unexpected token at line 3, column 9.",
    });
    expect(loc).toEqual({ line: 3, column: 9 });
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
});
