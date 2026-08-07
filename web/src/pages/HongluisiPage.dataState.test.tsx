// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiProblem } from "../contracts/api";
import HongluisiPage from "./HongluisiPage";

const apiMocks = vi.hoisted(() => ({
  getEngineStatus: vi.fn(),
  getEnginePreferences: vi.fn(),
  updateEnginePreferences: vi.fn(),
  listNetworkEvents: vi.fn(),
  refetchTools: vi.fn(),
}));

vi.mock("../hooks/useSystem", () => ({
  useTools: () => ({
    data: [],
    error: null,
    isLoading: false,
    refetch: apiMocks.refetchTools,
  }),
}));
vi.mock("../api/hongluisi", () => ({
  getEngineStatus: apiMocks.getEngineStatus,
  getEnginePreferences: apiMocks.getEnginePreferences,
  updateEnginePreferences: apiMocks.updateEnginePreferences,
}));
vi.mock("../api/network_events", () => ({
  listNetworkEvents: apiMocks.listNetworkEvents,
}));

function problem(status: number, code: string, message: string): ApiProblem {
  return {
    status,
    code,
    message,
    correlationId: `corr-${status}`,
    retryable: status >= 500,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <HongluisiPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
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
  apiMocks.getEngineStatus.mockResolvedValue({
    providers: { jina: "none", tavily: "none", firecrawl: "none" },
  });
  apiMocks.getEnginePreferences.mockResolvedValue({
    fetch_chain: [],
    search_provider: null,
    fallback_mode: null,
    scrapling_dynamic_enabled: false,
    scrapling_stealthy_enabled: false,
  });
  apiMocks.listNetworkEvents.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HongluisiPage truthful data states", () => {
  it("shows a recent-events 503 instead of the successful empty table copy", async () => {
    apiMocks.listNetworkEvents.mockRejectedValue(
      problem(503, "service-unavailable", "网络审计服务暂不可用"),
    );

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("网络审计服务暂不可用");
    expect(screen.queryByText("暂无网络调用记录")).not.toBeInTheDocument();
  });

  it("shows a 403 from a primary query instead of disabled provider cards", async () => {
    apiMocks.getEngineStatus.mockRejectedValue(
      problem(403, "permission-denied", "无权读取引擎状态"),
    );

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("无权查看此内容");
    expect(screen.getByRole("alert")).toHaveTextContent("无权读取引擎状态");
  });
});
