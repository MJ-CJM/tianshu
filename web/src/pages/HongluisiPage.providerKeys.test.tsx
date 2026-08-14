// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
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

describe("鸿胪寺搜索 provider 的 key 可见性", () => {
  it("标出哪个 provider 已配 key、哪个没配", async () => {
    apiMocks.getEngineStatus.mockResolvedValue({
      providers: { jina: "db", tavily: "none", firecrawl: "none" },
    });

    renderPage();

    // 选中一个没配 key 的 provider 会静默失败，状态必须当场可见
    expect(await screen.findByText("key 已配（db）")).toBeInTheDocument();
    expect(await screen.findByText("未配 key")).toBeInTheDocument();
  });

  it("env 来源的 key 同样算已配", async () => {
    apiMocks.getEngineStatus.mockResolvedValue({
      providers: { jina: "none", tavily: "env", firecrawl: "none" },
    });

    renderPage();

    expect(await screen.findByText("key 已配（env）")).toBeInTheDocument();
  });

  it("给出去哪儿配 key 的指路", async () => {
    apiMocks.getEngineStatus.mockResolvedValue({
      providers: { jina: "none", tavily: "none", firecrawl: "none" },
    });

    renderPage();

    expect(await screen.findByText(/Tavily \/ Jina 须先配好 API key/)).toBeInTheDocument();
    expect(await screen.findByText("前往配置搜索 key")).toBeInTheDocument();
  });
});
