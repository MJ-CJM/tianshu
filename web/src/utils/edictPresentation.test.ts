import { describe, expect, it } from "vitest";
import type { Edict, Memorial } from "../api/types";
import {
  deriveEdictWorkspacePhase,
  getEdictTaskKinds,
} from "./edictPresentation";

const baseEdict: Edict = {
  id: "edict-1",
  title: "Task",
  goal: "Run",
  context: null,
  status: "open",
  created_at: "2026-07-31T00:00:00Z",
  priority: "normal",
  review_policy: "never",
  schedule: {
    type: "immediate",
    at: null,
    cron: null,
    timezone: "Asia/Shanghai",
  },
  runtime: {
    timeout_seconds: 300,
    max_iterations: 1,
    max_concurrency: 1,
    retry_limit: 0,
    token_budget: null,
    cost_budget_cny: null,
    approval_required_tools: [],
    lifecycle_phase: "active",
    conversation: true,
    executor: "native",
  },
  constraints: [],
  output_format: null,
  source: "web",
  submitter: null,
  execution_profile: "foreground",
};

const completedMemorial = {
  status: "completed",
  review_status: "not_required",
} as Memorial;

describe("edict workspace presentation", () => {
  it("keeps overlapping task characteristics visible", () => {
    expect(getEdictTaskKinds(baseEdict)).toEqual(["immediate", "conversation"]);
    expect(
      getEdictTaskKinds({
        ...baseEdict,
        schedule: { ...baseEdict.schedule, type: "once" },
        execution_profile: "checkpointed",
        runtime: { ...baseEdict.runtime, executor: "keqing:codex" },
      }),
    ).toEqual(["scheduled_once", "long_running", "conversation", "keqing"]);
  });

  it("shows an open conversation with a completed run as waiting for follow-up", () => {
    expect(deriveEdictWorkspacePhase(baseEdict, completedMemorial)).toBe(
      "idle",
    );
  });

  it("keeps human intervention separate and higher priority than run phase", () => {
    const running = {
      status: "running",
      review_status: "not_required",
    } as Memorial;
    expect(deriveEdictWorkspacePhase(baseEdict, running, 1)).toBe(
      "needs_review",
    );
  });

  it("uses schedule and lifecycle state when no active run exists", () => {
    expect(
      deriveEdictWorkspacePhase(
        {
          ...baseEdict,
          schedule: { ...baseEdict.schedule, type: "cron" },
        },
        null,
      ),
    ).toBe("scheduled");
    expect(
      deriveEdictWorkspacePhase(
        {
          ...baseEdict,
          runtime: { ...baseEdict.runtime, lifecycle_phase: "paused" },
        },
        completedMemorial,
      ),
    ).toBe("paused");
  });

  it("lets the task container close the final display phase", () => {
    expect(
      deriveEdictWorkspacePhase(
        { ...baseEdict, status: "completed" },
        completedMemorial,
      ),
    ).toBe("completed");
    expect(
      deriveEdictWorkspacePhase(
        { ...baseEdict, status: "cancelled" },
        completedMemorial,
      ),
    ).toBe("cancelled");
    expect(
      deriveEdictWorkspacePhase(
        { ...baseEdict, status: "cancelled" },
        { ...completedMemorial, review_status: "pending" },
        1,
      ),
    ).toBe("cancelled");
  });

  it("shows a completed one-shot execution as completed", () => {
    expect(
      deriveEdictWorkspacePhase(
        {
          ...baseEdict,
          runtime: { ...baseEdict.runtime, conversation: false },
        },
        completedMemorial,
      ),
    ).toBe("completed");
  });
});
