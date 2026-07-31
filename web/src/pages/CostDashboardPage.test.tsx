// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  useCostSummary: vi.fn(),
  useCostRecords: vi.fn(),
  useCostBudget: vi.fn(),
}));

vi.mock("../hooks/useCost", () => ({
  ...hookMocks,
  useSetCostBudget: vi.fn(),
}));

vi.mock("../components/cost/ProviderPricingCard", () => ({ default: () => null }));

import CostDashboardPage from "./CostDashboardPage";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CostDashboardPage data truth", () => {
  it("does not turn a ledger outage into zero cost", () => {
    const error = {
      status: 503,
      code: "service-unavailable",
      message: "成本账本暂不可用",
      correlationId: "cost-correlation",
      retryable: true,
    };
    hookMocks.useCostSummary.mockReturnValue({
      data: undefined,
      error,
      isLoading: false,
      refetch: vi.fn(),
    });
    hookMocks.useCostRecords.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
      refetch: vi.fn(),
    });
    hookMocks.useCostBudget.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
      refetch: vi.fn(),
    });

    render(<CostDashboardPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("成本账本暂不可用");
    expect(screen.queryByText("¥0")).not.toBeInTheDocument();
  });
});
