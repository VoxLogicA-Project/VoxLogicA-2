import { beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

import { resolvePlaygroundValue, resolvePlaygroundValuePage } from "$lib/api/client.js";
import { clearComputeActivity, computeActivity } from "$lib/stores/computeActivity.js";

const makeResponse = (payload) => ({
  ok: true,
  status: 200,
  statusText: "OK",
  text: async () => JSON.stringify(payload ?? {}),
});

describe("api client compute activity", () => {
  beforeEach(() => {
    clearComputeActivity();
    globalThis.fetch = vi.fn();
  });

  it("records activity for value and page requests", async () => {
    globalThis.fetch
      .mockResolvedValueOnce(makeResponse({ materialization: "computed", compute_status: "completed" }))
      .mockResolvedValueOnce(
        makeResponse({
          materialization: "computed",
          compute_status: "completed",
          page: { offset: 0, limit: 8, items: [], has_more: false, next_offset: null },
        }),
      );

    await resolvePlaygroundValue({ program: "x = 1", variable: "x", path: "/", enqueue: false });
    await resolvePlaygroundValuePage({ program: "xs = range(0,1)", variable: "xs", path: "/", offset: 0, limit: 8 });

    const entries = get(computeActivity);
    const types = entries.map((entry) => entry.type);
    expect(types).toContain("value.request");
    expect(types).toContain("value.response");
    expect(types).toContain("page.request");
    expect(types).toContain("page.response");
  });

  it("sends explicit ui_awaited intent for value and page requests", async () => {
    globalThis.fetch
      .mockResolvedValueOnce(makeResponse({ materialization: "pending", compute_status: "queued" }))
      .mockResolvedValueOnce(makeResponse({ materialization: "pending", compute_status: "queued", page: { items: [] } }));

    await resolvePlaygroundValue({
      program: "x = 1",
      variable: "x",
      path: "/",
      enqueue: true,
      uiAwaited: true,
      interaction: { intent: "run-primary", source: "run-button", sequence: 4, age_ms: 0 },
    });
    await resolvePlaygroundValuePage({
      program: "xs = range(0, 10)",
      variable: "xs",
      path: "/",
      offset: 0,
      limit: 8,
      enqueue: true,
      uiAwaited: false,
      interaction: { intent: "page-nav", source: "viewer", sequence: 5, age_ms: 3 },
    });

    const firstPayload = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    const secondPayload = JSON.parse(globalThis.fetch.mock.calls[1][1].body);

    expect(firstPayload.ui_awaited).toBe(true);
    expect(secondPayload.ui_awaited).toBe(false);
    expect(firstPayload.interaction.intent).toBe("run-primary");
    expect(secondPayload.interaction.intent).toBe("page-nav");
  });
});
