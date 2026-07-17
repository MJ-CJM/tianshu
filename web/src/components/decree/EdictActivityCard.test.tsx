// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Edict, Memorial, PendingToolCall } from "../../api/types";
import EdictActivityCard from "./EdictActivityCard";

vi.mock("../../i18n", () => ({
  useT: () => (key: string) => key,
}));
const EDICT = {
  id: "edict-read-only",
  title: "只读待处置",
  goal: "统一从详情裁决",
  context: null,
  status: "open",
  created_at: "2026-07-17T08:00:00Z",
  priority: "normal",
  review_policy: "always",
  schedule: { type: "immediate", at: null, cron: null, timezone: "Asia/Shanghai" },
  runtime: {
    timeout_seconds: 300,
    max_iterations: 20,
    max_concurrency: 1,
    retry_limit: 0,
    token_budget: null,
    cost_budget_cny: null,
    approval_required_tools: [],
    lifecycle_phase: "active",
  },
  constraints: [],
  output_format: null,
  source: "web",
  submitter: "user:owner",
} as Edict;

const MEMORIAL = {
  id: "memorial-read-only",
  edict_id: EDICT.id,
  instruction: "旧奏折待审核",
  status: "needs_review",
  summary: null,
  result: null,
  usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  error: null,
  created_at: "2026-07-17T08:00:00Z",
  started_at: null,
  completed_at: null,
  attempt: 1,
  parent_memorial_id: null,
  review_status: "pending",
  audit: null,
  artifacts: [],
  timeline: [],
  persona_id: null,
  dag_node_id: null,
} as Memorial;

const PENDING_TOOL = {
  decision_request_id: "decision-tool",
  memorial_id: MEMORIAL.id,
  edict_id: EDICT.id,
  tool_name: "legacy.tool",
  rule_id: "approval_required",
  reason: "旧工具审批",
  tool_tier: "write",
  args_summary: {},
  created_at: "2026-07-17T08:01:00Z",
} as PendingToolCall;

function LocationProbe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(cleanup);

describe("EdictActivityCard decision authority", () => {
  it("renders legacy pending records read-only and guides to the composed detail decision", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/approvals"]}>
        <LocationProbe />
        <EdictActivityCard
          edict={EDICT}
          latestMemorial={MEMORIAL}
          pendingToolCalls={[PENDING_TOOL]}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("legacy.tool")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pendingTool\.action/ })).not.toBeInTheDocument();
    for (const action of ["action.approve", "action.reject", "action.retry", "action.amend", "action.cancel"]) {
      expect(screen.queryByRole("button", { name: new RegExp(action.replace(".", "\\.")) })).not.toBeInTheDocument();
    }

    await user.click(screen.getByRole("button", { name: /comp\.edictActivity\.openDecision/ }));
    expect(screen.getByLabelText("location")).toHaveTextContent("/edicts/edict-read-only");
  });
});
