// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiResponse,
  PendingToolCall,
  ToolDecisionResult,
} from "../../api/types";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../../hooks/useApprovals", () => ({
  useSubmitToolDecision: () => ({ mutate: mocks.mutate, isPending: false }),
}));
vi.mock("../../i18n", () => ({
  useT: () => (key: string, vars?: Record<string, string | number>) =>
    vars
      ? `${key} ${Object.entries(vars)
          .map(([name, value]) => `${name}=${value}`)
          .join(" ")}`
      : key,
}));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: {
          success: mocks.success,
          warning: mocks.warning,
          info: mocks.info,
          error: mocks.error,
        },
      }),
    },
  };
});

import PendingToolCallCard from "./PendingToolCallCard";

const PENDING: PendingToolCall = {
  decision_request_id: "01DECISIONAAAA11111111111111",
  memorial_id: "memorial-web-race",
  edict_id: "edict-web-race",
  tool_name: "shell_exec",
  rule_id: "approval_required",
  reason: null,
  tool_tier: "T2_EXEC",
  args_summary: { command: "git status" },
  created_at: "2026-07-15T10:00:00Z",
};

function response(
  updates: Partial<ToolDecisionResult>,
): ApiResponse<ToolDecisionResult> {
  return {
    success: true,
    data: {
      decision_request_id: PENDING.decision_request_id,
      memorial_id: PENDING.memorial_id,
      edict_id: PENDING.edict_id,
      action: "approve",
      comment: "reviewed",
      actor: "telegram:telegram-primary:7",
      grant_scope: "once",
      grant_reason: null,
      requested_grant_scope: "once",
      grant_downgraded: false,
      grant_downgrade_reason: null,
      resolved_at: "2026-07-15T10:01:00Z",
      ...updates,
    },
    error: null,
    metadata: null,
  };
}

function mutationCallbacks() {
  return mocks.mutate.mock.calls[mocks.mutate.mock.calls.length - 1]?.[1] as {
    onSuccess: (result: ApiResponse<ToolDecisionResult>) => void;
  };
}

function lastMutationRequest(): unknown {
  return mocks.mutate.mock.calls[mocks.mutate.mock.calls.length - 1]?.[0];
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  for (const mock of Object.values(mocks)) mock.mockReset();
});

afterEach(cleanup);

describe("durable tool decision result messages", () => {
  it("reports the durable reject winner after a local approve loses the race", async () => {
    render(<PendingToolCallCard pending={PENDING} />);
    fireEvent.click(screen.getByRole("button", { name: /pendingTool\.action/ }));
    fireEvent.click(await screen.findByRole("button", { name: /pendingTool\.approve/ }));

    expect(lastMutationRequest()).toMatchObject({ action: "approve" });
    act(() => mutationCallbacks().onSuccess(response({ action: "reject", grant_scope: null })));

    expect(mocks.warning).toHaveBeenCalledWith(
      "toast.toolAlreadyDecided action=reject scope=none",
    );
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("reports the returned effective scope when dangerous always is downgraded", async () => {
    render(<PendingToolCallCard pending={PENDING} />);
    fireEvent.click(screen.getByRole("button", { name: /pendingTool\.action/ }));
    fireEvent.click(await screen.findByText("pendingTool.scope.always"));
    fireEvent.click(screen.getByRole("button", { name: /pendingTool\.approve/ }));

    expect(lastMutationRequest()).toMatchObject({
      action: "approve",
      grant_scope: "always",
    });
    act(() =>
      mutationCallbacks().onSuccess(
        response({
          grant_scope: "once",
          requested_grant_scope: "always",
          grant_downgraded: true,
          grant_downgrade_reason: "bash-family tools cannot be permanent",
        }),
      ),
    );

    expect(mocks.warning).toHaveBeenCalledWith(
      "toast.toolScopeDowngraded tool=shell_exec requestedScope=always scope=once",
    );
    expect(mocks.success).not.toHaveBeenCalled();
  });
});
