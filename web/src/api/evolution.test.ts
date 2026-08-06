import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { get } }));

import { getEvolutionCenterSnapshot } from "./evolution";

describe("Evolution Center API", () => {
  beforeEach(() => get.mockReset());

  it("reads the single authenticated snapshot endpoint", async () => {
    const snapshot = {
      schema_version: 1,
      status: "not_enabled",
      reason_code: "s5_governed_evolution_not_enabled",
      candidates: [],
      routing: [],
      last_gate_hash: null,
    };
    get.mockResolvedValue({
      data: { data: snapshot, correlation_id: "corr-evolution" },
    });

    await expect(getEvolutionCenterSnapshot()).resolves.toEqual(snapshot);
    expect(get).toHaveBeenCalledWith("/evolution");
  });
});
