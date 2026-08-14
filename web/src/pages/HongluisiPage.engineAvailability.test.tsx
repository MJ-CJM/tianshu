// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HongluisiPage from "./HongluisiPage";

const apiMocks = vi.hoisted(() => ({
  getEngineStatus: vi.fn(),
  getEnginePreferences: vi.fn(),
  updateEnginePreferences: vi.fn(),
  listNetworkEvents: vi.fn(),
}));

vi.mock("../hooks/useSystem", () => ({
  useTools: () => ({ data: [], error: null, isLoading: false, refetch: vi.fn() }),
}));
vi.mock("../api/hongluisi", () => ({
  getEngineStatus: apiMocks.getEngineStatus,
  getEnginePreferences: apiMocks.getEnginePreferences,
  updateEnginePreferences: apiMocks.updateEnginePreferences,
}));
vi.mock("../api/network_events", () => ({
  listNetworkEvents: apiMocks.listNetworkEvents,
}));

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

/** 只取当前展开的下拉——已关闭的下拉节点仍留在 DOM 里，全局查会误命中 */
function visibleOptionLabels(): string[] {
  return [
    ...document.querySelectorAll(
      ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content",
    ),
  ].map((el) => el.textContent ?? "");
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

describe("抓取引擎可用性", () => {
  it("标出没装的引擎——选了也是空转", async () => {
    const user = userEvent.setup();
    apiMocks.getEngineStatus.mockResolvedValue({
      providers: { jina: "none", tavily: "none", firecrawl: "none" },
      fetch_engines: ["local", "firecrawl"], // scrapling 三兄弟未安装
    });

    renderPage();
    await user.click(await screen.findByRole("combobox"));
    const labels = visibleOptionLabels();

    // 装了的照常显示，不加噪音
    expect(labels).toContain("local (trafilatura)");
    expect(labels).toContain("firecrawl");
    // 没装的当场说明「选了没用」，而不是等运行时静默 skipped
    expect(
      labels.filter((l) => l.startsWith("scrapling") && l.includes("空转")),
    ).toHaveLength(3);
  });

  it("引擎全部就绪时不加任何标注", async () => {
    const user = userEvent.setup();
    apiMocks.getEngineStatus.mockResolvedValue({
      providers: { jina: "db", tavily: "db", firecrawl: "db" },
      fetch_engines: [
        "scrapling",
        "scrapling_dynamic",
        "scrapling_stealthy",
        "local",
        "jina",
        "firecrawl",
      ],
    });

    renderPage();
    await user.click(await screen.findByRole("combobox"));

    expect(visibleOptionLabels().some((l) => l.includes("空转"))).toBe(false);
  });

  it("拿不到 engine-status 时不冒充「已安装」", async () => {
    const user = userEvent.setup();
    apiMocks.getEngineStatus.mockResolvedValue({
      providers: { jina: "none", tavily: "none", firecrawl: "none" },
      fetch_engines: [],
    });

    renderPage();
    await user.click(await screen.findByRole("combobox"));

    // 宁可全标未装，也不能让用户以为选了就能用
    expect(visibleOptionLabels().every((l) => l.includes("空转"))).toBe(true);
  });
});
