import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { post } }));

import { previewEdictGovernance } from "./edicts";

describe("governance preview API", () => {
  beforeEach(() => post.mockReset());

  it("posts the exact draft to the preview endpoint and returns structured truth", async () => {
    const preview = {
      compatible: true,
      requested_contract: { schema_version: "1" },
      requested_contract_hash: "a".repeat(64),
      effective_contract: { schema_version: "1" },
      mandatory_mismatches: [],
      execution_mode: "single" as const,
      execution_mode_mismatches: [],
      advisory_gaps: ["durable_resume"],
      executor_level: "contained" as const,
      experimental: false,
      manifest_hash: "b".repeat(64),
      runtime_probe_id: "host-test",
    };
    post.mockResolvedValue({ data: { success: true, data: preview } });
    const request = { goal: "governed", runtime: { executor: "native" } };

    await expect(previewEdictGovernance(request)).resolves.toEqual(preview);
    expect(post).toHaveBeenCalledWith("/edicts/governance/preview", request);
  });
});
