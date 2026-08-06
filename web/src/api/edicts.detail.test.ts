import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { get, post } }));

import {
  getEdictDetailSnapshot,
  replayGovernedEdict,
  resolveEdictDecision,
} from "./edicts";

describe("durable Edict detail API", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("reads the single principal-scoped composed detail endpoint", async () => {
    const snapshot = { schema_version: 1, edict: { id: "edict/1" } };
    get.mockResolvedValue({
      data: { data: snapshot, correlation_id: "corr-1" },
    });

    await expect(getEdictDetailSnapshot("edict/1")).resolves.toEqual(snapshot);
    expect(get).toHaveBeenCalledWith("/edicts/edict%2F1/detail");
  });

  it("submits reason, expected version and the kind-specific durable payload", async () => {
    post.mockResolvedValue({
      data: {
        data: {
          action: "approve",
          reason: "已核验恢复点",
          actor_principal_id: "user:owner",
          resolved_at: "2026-07-17T09:00:00Z",
        },
        status: "resolved",
        version: 5,
      },
    });

    await expect(
      resolveEdictDecision({
        decisionRequestId: "decision/1",
        kind: "governed_apply",
        action: "approve",
        reason: "已核验恢复点",
        expectedVersion: 4,
      }),
    ).resolves.toMatchObject({
      status: "resolved",
      version: 5,
      actor: "user:owner",
    });
    expect(post).toHaveBeenCalledWith("/decisions/decision%2F1/resolve", {
      action: "approve",
      reason: "已核验恢复点",
      expected_version: 4,
      payload: { schema_version: 1 },
    });
  });

  it("binds tool guidance to the durable reason instead of dropping it", async () => {
    post.mockResolvedValue({
      data: {
        data: {
          action: "guide",
          reason: "仅检查工作区",
          actor_principal_id: "user:owner",
          resolved_at: "2026-07-17T09:00:00Z",
        },
        status: "resolved",
        version: 2,
      },
    });

    await resolveEdictDecision({
      decisionRequestId: "decision-tool",
      kind: "tool",
      action: "guide",
      reason: "仅检查工作区",
      expectedVersion: 1,
    });

    expect(post).toHaveBeenCalledWith("/decisions/decision-tool/resolve", {
      action: "guide",
      reason: "仅检查工作区",
      expected_version: 1,
      payload: { schema_version: 1, guidance: "仅检查工作区" },
    });
  });

  it("replay creates a new governed request with no browser actor or execution call", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "replay-idempotency-key"),
    });
    const requestedContract = {
      schema_version: "1",
      objective: {
        goal: "verify release",
        context: null,
        constraints: [],
        output_format: null,
      },
    };
    post.mockResolvedValue({
      data: { success: true, data: { id: "edict-replay" } },
    });

    await expect(
      replayGovernedEdict({
        title: "Release validation",
        goal: "verify release",
        context: null,
        priority: "normal",
        governanceContract: requestedContract,
      }),
    ).resolves.toEqual("edict-replay");

    expect(post).toHaveBeenCalledTimes(1);
    const [url, body, config] = post.mock.calls[0]!;
    expect(url).toBe("/edicts");
    expect(url).not.toMatch(/execute|run|replay/);
    expect(body).toMatchObject({
      title: "Release validation",
      goal: "verify release",
      governance_contract: requestedContract,
      idempotency_key: "replay-idempotency-key",
    });
    expect(body).not.toHaveProperty("actor");
    expect(body).not.toHaveProperty("submitter");
    expect(config).toEqual({
      headers: { "Idempotency-Key": "replay-idempotency-key" },
    });
  });
});
