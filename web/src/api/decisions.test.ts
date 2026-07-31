import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { get } }));

import { listPendingDecisions } from "./decisions";

describe("pending decisions API", () => {
  beforeEach(() => get.mockReset());

  it("reads the authenticated durable queue with a bounded limit", async () => {
    const items = [
      {
        decision_request_id: "decision-1",
        kind: "outer_loop",
        edict_id: "edict-1",
        status: "pending",
      },
    ];
    get.mockResolvedValue({ data: { items, correlation_id: "corr-1" } });

    await expect(listPendingDecisions()).resolves.toEqual(items);
    expect(get).toHaveBeenCalledWith("/decisions", { params: { limit: 200 } });
  });
});
