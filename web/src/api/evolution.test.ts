import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { get, put } }));

import {
  type EvolutionCandidateSummaryV1,
  type EvolutionPolicyV1,
  getEvolutionCenterSnapshot,
  listEvolutionPolicies,
  putEvolutionPolicy,
} from "./evolution";

describe("Evolution Center API", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
  });

  it("accepts executor in candidate and policy kind contracts", () => {
    const candidateKind: EvolutionCandidateSummaryV1["kind"] = "executor";
    const policyKind: EvolutionPolicyV1["kind"] = "executor";

    expect(candidateKind).toBe("executor");
    expect(policyKind).toBe("executor");
  });

  it("reads the single authenticated snapshot endpoint", async () => {
    const snapshot = {
      schema_version: 1,
      status: "not_enabled",
      reason_code: "s5_governed_evolution_not_enabled",
      routing_enabled: true,
      candidates: [],
      routing: [],
      last_gate_hash: null,
    };
    get.mockResolvedValue({ data: { data: snapshot, correlation_id: "corr-evolution" } });

    await expect(getEvolutionCenterSnapshot()).resolves.toEqual(snapshot);
    expect(get).toHaveBeenCalledWith("/evolution");
  });

  it("reads the admin policy list through one named contract", async () => {
    const policy = {
      subject_key: "skill:reviewer",
      kind: "skill",
      mode: "frozen",
      max_canary_basis_points: 500,
      version: 3,
      updated_at: "2026-08-26T00:00:00+00:00",
    };
    get.mockResolvedValue({ data: { data: [policy], correlation_id: "corr-policy" } });

    await expect(listEvolutionPolicies()).resolves.toEqual([policy]);
    expect(get).toHaveBeenCalledWith(
      "/evolution/policies",
      { silentCodes: [403] },
    );
  });

  it("writes an exact CAS policy body and encodes the subject path", async () => {
    const policy = {
      subject_key: "skill:reviewer/tools",
      kind: "skill" as const,
      mode: "manual" as const,
      max_canary_basis_points: 250,
      version: 5,
      updated_at: "2026-08-26T00:00:00+00:00",
    };
    put.mockResolvedValue({ data: { data: policy, correlation_id: "corr-put" } });

    await expect(
      putEvolutionPolicy({
        subject_key: policy.subject_key,
        kind: policy.kind,
        mode: policy.mode,
        max_canary_basis_points: policy.max_canary_basis_points,
        expected_version: 4,
      }),
    ).resolves.toEqual(policy);
    expect(put).toHaveBeenCalledWith(
      "/evolution/policies/skill%3Areviewer%2Ftools",
      {
        kind: "skill",
        mode: "manual",
        max_canary_basis_points: 250,
        expected_version: 4,
      },
      { silentCodes: [403, 409] },
    );
  });
});
