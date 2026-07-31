// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
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
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <EvalsPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "考成院" })).toBeInTheDocument();
    expect(screen.getAllByText("Beta")).toHaveLength(2);
    expect(
      screen.getByText("查看真实考卷、运行分数、历次差异、失事分布与靡费。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("高靡费跑批仍由 CLI 发起；单次考成不会自行触发晋升。"),
    ).toBeInTheDocument();
  });
});
