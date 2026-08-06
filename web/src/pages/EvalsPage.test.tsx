// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getEvalRun: vi.fn(),
  listEvalRuns: vi.fn(),
  listEvalSets: vi.fn(),
}));

vi.mock("../api/evals", () => apiMocks);

import EvalsPage from "./EvalsPage";

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
  apiMocks.listEvalRuns.mockResolvedValue({ success: true, data: [] });
  apiMocks.listEvalSets.mockResolvedValue({ success: true, data: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EvalsPage maturity boundary", () => {
  it("labels the page as Beta and states the runnable and CLI boundaries", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <EvalsPage />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "考成院" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Beta")).toHaveLength(2);
    expect(
      screen.getByText("查看真实考卷、运行分数、历次差异、失事分布与靡费。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("高靡费跑批仍由 CLI 发起；单次考成不会自行触发晋升。"),
    ).toBeInTheDocument();
  });

  it("lets keyboard users select a run through its ID button", async () => {
    const user = userEvent.setup();
    const fitness = {
      score: 1,
      samples: 1,
      success_rate: 1,
      audit_rate: 1,
      retry_score: 1,
      cost_score: 1,
      feedback: 0,
    };
    const runs = [
      {
        id: "run-first-0001",
        eval_set_name: "首卷",
        eval_set_fingerprint: "first",
        target: "target-first",
        fitness,
        n: 1,
        truncated: false,
        delta_vs_prev: null,
        created_at: "2026-07-31T08:00:00Z",
      },
      {
        id: "run-second-0002",
        eval_set_name: "次卷",
        eval_set_fingerprint: "second",
        target: "target-second",
        fitness,
        n: 1,
        truncated: false,
        delta_vs_prev: 0,
        created_at: "2026-07-31T09:00:00Z",
      },
    ];
    apiMocks.listEvalRuns.mockResolvedValue({ success: true, data: runs });
    apiMocks.getEvalRun.mockImplementation(async (id: string) => ({
      success: true,
      data: {
        ...runs.find((run) => run.id === id)!,
        stats: {},
        goal_results: [],
        failure_distribution: [],
      },
    }));
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <EvalsPage />
      </QueryClientProvider>,
    );

    const secondRunButton = await screen.findByRole("button", {
      name: /run-second/,
    });
    secondRunButton.focus();
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(apiMocks.getEvalRun).toHaveBeenCalledWith("run-second-0002"),
    );
    expect(await screen.findByText("target-second")).toBeInTheDocument();
  });
});
