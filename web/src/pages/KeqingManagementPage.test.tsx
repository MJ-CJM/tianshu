// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getKeqingStatus: vi.fn(),
  getAgentConfig: vi.fn(),
  updateAgentConfig: vi.fn(),
}));

vi.mock("../api/config", () => apiMocks);

import KeqingManagementPage from "./KeqingManagementPage";

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
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("KeqingManagementPage data truth", () => {
  it("keeps installed backend versions synchronized while the page stays open", async () => {
    apiMocks.getKeqingStatus.mockResolvedValue({
      backends: [],
      gateway_enabled: false,
    });
    apiMocks.getAgentConfig.mockResolvedValue({});
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <KeqingManagementPage />
      </QueryClientProvider>,
    );

    await screen.findByText("治理默认");
    const statusQuery = client
      .getQueryCache()
      .find({ queryKey: ["keqing-status"] });
    const statusOptions = statusQuery?.options as Record<string, unknown> | undefined;
    expect(statusOptions?.refetchInterval).toBe(15_000);
    expect(statusOptions?.refetchIntervalInBackground).toBe(false);
    expect(statusOptions?.refetchOnMount).toBe("always");
    expect(statusOptions?.refetchOnWindowFocus).toBe("always");
  });

  it("does not expose the unwired credential gateway as a runnable control", async () => {
    apiMocks.getKeqingStatus.mockResolvedValue({
      backends: [],
      gateway_enabled: false,
    });
    apiMocks.getAgentConfig.mockResolvedValue({
      keqing_default_models: {},
      keqing_gateway_enabled: true,
      keqing_per_run_budget_cny: 0,
      keqing_model_allowlist: "openai/gpt-5",
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <KeqingManagementPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("治理默认")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "客卿" })).toBeInTheDocument();
    expect(screen.getAllByText("实验")).toHaveLength(2);
    expect(
      screen.getByText(
        "查验 Claude Code、Codex、Pi、OpenCode，并配置模型与单次靡费默认值。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "凭证由各 CLI 自理；可靠事前动作拦截与 Provider 侧硬成本上限尚未具备。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByText("模型白名单")).not.toBeInTheDocument();
  });

  it("does not present a status outage as an empty backend registry", async () => {
    apiMocks.getKeqingStatus.mockRejectedValue({
      status: 503,
      code: "service-unavailable",
      message: "客卿状态服务暂不可用",
      correlationId: "keqing-correlation",
      retryable: true,
    });
    apiMocks.getAgentConfig.mockResolvedValue({});
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <KeqingManagementPage />
      </QueryClientProvider>,
    );

    const errorText = await screen.findByText("客卿状态服务暂不可用");
    expect(errorText.closest('[role="alert"]')).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
