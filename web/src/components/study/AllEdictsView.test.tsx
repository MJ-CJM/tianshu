// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listEdicts: vi.fn(),
  deleteEdict: vi.fn(),
}));
const approvals = vi.hoisted(() => ({
  useEdictLatestMemorials: vi.fn(),
  usePendingToolCalls: vi.fn(),
  usePendingDecisions: vi.fn(),
}));

vi.mock("../../api/edicts", () => api);
vi.mock("../../hooks/useApprovals", () => approvals);
vi.mock("../edict/EdictTable", () => ({
  default: ({
    edicts,
    pendingDecisionCounts,
    progressUnavailable,
  }: {
    edicts: Array<{ title: string }>;
    pendingDecisionCounts: Record<string, number>;
    progressUnavailable: boolean;
  }) => (
    <div>
      {edicts.map((edict) => edict.title).join(",")}
      <span>pending:{pendingDecisionCounts["edict-1"] ?? 0}</span>
      <span>progress-unavailable:{String(progressUnavailable)}</span>
    </div>
  ),
}));

import AllEdictsView from "./AllEdictsView";

const refetchMemorials = vi.fn();
const refetchTools = vi.fn();
const refetchDecisions = vi.fn();

function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AllEdictsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.listEdicts.mockResolvedValue({
    data: [
      {
        id: "edict-1",
        title: "Visible task",
        goal: "Run",
      },
    ],
    metadata: { total: 1 },
  });
  approvals.useEdictLatestMemorials.mockReturnValue({
    data: { data: { "edict-1": null } },
    isFetching: false,
    refetch: refetchMemorials,
  });
  approvals.usePendingToolCalls.mockReturnValue({
    data: [],
    isFetching: false,
    refetch: refetchTools,
  });
  approvals.usePendingDecisions.mockReturnValue({
    data: [{ decision_request_id: "decision-1", edict_id: "edict-1" }],
    isFetching: false,
    refetch: refetchDecisions,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AllEdictsView workspace", () => {
  it("loads every task status by default and enriches the visible page", async () => {
    renderView();

    expect(await screen.findByText("Visible task")).toBeInTheDocument();
    expect(api.listEdicts).toHaveBeenCalledWith(
      expect.objectContaining({ status: undefined, offset: 0 }),
    );
    expect(approvals.useEdictLatestMemorials).toHaveBeenLastCalledWith(
      ["edict-1"],
      true,
    );
    expect(screen.getByText("pending:1")).toBeInTheDocument();
  });

  it("refreshes task, progress, and intervention data together", async () => {
    renderView();
    await screen.findByText("Visible task");

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));

    expect(refetchMemorials).toHaveBeenCalledOnce();
    expect(refetchTools).toHaveBeenCalledOnce();
    expect(refetchDecisions).toHaveBeenCalledOnce();
    expect(api.listEdicts).toHaveBeenCalledTimes(2);
  });

  it("keeps tasks visible and marks progress unavailable when enrichment fails", async () => {
    approvals.useEdictLatestMemorials.mockReturnValue({
      data: undefined,
      error: new Error("progress unavailable"),
      isFetching: false,
      refetch: refetchMemorials,
    });

    renderView();

    expect(await screen.findByText("Visible task")).toBeInTheDocument();
    expect(screen.getByText("部分进度暂未更新")).toBeInTheDocument();
    expect(screen.getByText("progress-unavailable:true")).toBeInTheDocument();
  });
});
