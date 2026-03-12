import { beforeEach, describe, expect, it } from "vitest";
import { get } from "svelte/store";

import { clearComputeActivity, computeActivity, ongoingComputeActivity, pushComputeActivity } from "./computeActivity.js";

describe("computeActivity store", () => {
  beforeEach(() => {
    clearComputeActivity();
  });

  it("tracks active operations from start to finish", () => {
    pushComputeActivity({
      type: "resolve.primary",
      phase: "start",
      trackActive: true,
      operationKey: "resolve:test",
      summary: "Resolving x /",
      variable: "x",
      path: "/",
      status: "running",
    });

    expect(get(ongoingComputeActivity)).toHaveLength(1);
    expect(get(ongoingComputeActivity)[0]?.summary).toBe("Resolving x /");

    pushComputeActivity({
      type: "resolve.primary",
      phase: "update",
      trackActive: true,
      operationKey: "resolve:test",
      summary: "Response received for x /",
      variable: "x",
      path: "/",
      status: "persisting",
      detail: "elapsed=42.0ms",
    });

    expect(get(ongoingComputeActivity)).toHaveLength(1);
    expect(get(ongoingComputeActivity)[0]?.status).toBe("persisting");
    expect(get(ongoingComputeActivity)[0]?.detail).toBe("elapsed=42.0ms");

    pushComputeActivity({
      type: "resolve.primary",
      phase: "finish",
      trackActive: true,
      final: true,
      operationKey: "resolve:test",
      summary: "Resolved x /",
      variable: "x",
      path: "/",
      status: "completed",
    });

    expect(get(ongoingComputeActivity)).toHaveLength(0);
    expect(get(computeActivity).map((entry) => entry.summary)).toContain("Resolved x /");
  });

  it("updates active operations without adding history when skipHistory is set", () => {
    pushComputeActivity({
      type: "resolve.poll",
      phase: "start",
      trackActive: true,
      operationKey: "poll:test",
      summary: "Watching x /",
      variable: "x",
      path: "/",
      status: "running",
    });

    const historyBefore = get(computeActivity).length;
    pushComputeActivity({
      type: "resolve.poll",
      phase: "update",
      trackActive: true,
      skipHistory: true,
      operationKey: "poll:test",
      summary: "Still waiting on x /",
      variable: "x",
      path: "/",
      status: "running",
      detail: "ticks=2",
    });

    expect(get(computeActivity)).toHaveLength(historyBefore);
    expect(get(ongoingComputeActivity)[0]?.detail).toBe("ticks=2");
  });
});
