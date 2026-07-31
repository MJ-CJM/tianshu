// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
  getEffectivePricing: vi.fn(),
  getDefaultPricingTable: vi.fn(),
  resetProviderPricing: vi.fn(),
  updateProviderPricing: vi.fn(),
}));

vi.mock("../../api/providers", () => apiMocks);

import ProviderPricingCard from "./ProviderPricingCard";

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
    })),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProviderPricingCard data truth", () => {
  it("shows a retryable error instead of an empty pricing table", async () => {
    apiMocks.getProviders.mockRejectedValue({
      status: 503,
      code: "service-unavailable",
      message: "计价配置暂不可用",
      correlationId: "pricing-test",
      retryable: true,
    });

    render(<ProviderPricingCard />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "计价配置暂不可用",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("marks an individual pricing lookup as unavailable", async () => {
    apiMocks.getProviders.mockResolvedValue([
      {
        name: "primary",
        model: "test-model",
        status: "active",
        priority: 1,
        rpm_limit: null,
        cost_per_1k_prompt: null,
        cost_per_1k_cache_read: null,
        cost_per_1k_completion: null,
      },
    ]);
    apiMocks.getEffectivePricing.mockRejectedValue(new Error("pricing down"));

    render(<ProviderPricingCard />);

    expect(await screen.findByText("服务暂不可用")).toBeInTheDocument();
    expect(screen.getByText("primary")).toBeInTheDocument();
    expect(screen.queryByText("default")).not.toBeInTheDocument();
  });
});
