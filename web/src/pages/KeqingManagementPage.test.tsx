// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import KeqingManagementPage from "./KeqingManagementPage";

// antd 组件依赖 matchMedia/getComputedStyle,jsdom 无原生实现——inline polyfill(同现有页面测试)。
beforeAll(() => {
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

const mocks = vi.hoisted(() => ({
  getKeqingStatus: vi.fn(),
  getAgentConfig: vi.fn(),
  updateAgentConfig: vi.fn(),
}));

vi.mock("../api/config", () => ({
  getKeqingStatus: mocks.getKeqingStatus,
  getAgentConfig: mocks.getAgentConfig,
  updateAgentConfig: mocks.updateAgentConfig,
}));

// i18n:key 透传,便于对稳定 key 断言
vi.mock("../i18n", () => ({ useT: () => (k: string) => k }));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <KeqingManagementPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("KeqingManagementPage", () => {
  it("renders backend registry with version drift + capabilities (外臣能力,非人格)", async () => {
    mocks.getKeqingStatus.mockResolvedValue({
      gateway_enabled: false,
      backends: [
        {
          id: "keqing:pi",
          backend: "pi",
          binary: "pi",
          installed: true,
          installed_version: "0.79.3",
          pinned_version: "0.81.1",
          version_drift: true,
          capabilities: {
            permission_shaping: "none",
            hooks: "none",
            stop_gate: true,
            session_resume: true,
            interject: true,
            usage_reporting: "full",
          },
          credential_status: "self-managed",
        },
      ],
    });
    mocks.getAgentConfig.mockResolvedValue({
      keqing_default_models: {},
      keqing_gateway_enabled: false,
      keqing_per_run_budget_cny: 0,
      keqing_model_allowlist: "",
    });
    renderPage();

    await waitFor(() => expect(screen.getByText("keqing:pi")).toBeInTheDocument());
    expect(screen.getByText(/0\.79\.3/)).toBeInTheDocument(); // 安装版本
    expect(screen.getByText("keqing.drift")).toBeInTheDocument(); // 漂移标
    expect(screen.getByText("keqing.cap.resume")).toBeInTheDocument(); // 能力声明
    expect(screen.getByText("keqing.cred.selfManaged")).toBeInTheDocument(); // 客卿自管
  });

  it("hydrates governance defaults form from agent-config", async () => {
    mocks.getKeqingStatus.mockResolvedValue({ gateway_enabled: false, backends: [] });
    mocks.getAgentConfig.mockResolvedValue({
      keqing_default_models: { "claude-code": "anthropic/claude-opus" },
      keqing_gateway_enabled: true,
      keqing_per_run_budget_cny: 5,
      keqing_model_allowlist: "",
    });
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("keqing.section.governance")).toBeInTheDocument(),
    );
    // 默认模型输入回填
    await waitFor(() =>
      expect(screen.getByDisplayValue("anthropic/claude-opus")).toBeInTheDocument(),
    );
  });
});
