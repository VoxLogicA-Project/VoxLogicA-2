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
});
