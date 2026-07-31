// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getEstop: vi.fn(),
  engageEstop: vi.fn(),
  resumeEstop: vi.fn(),
}));

vi.mock("../../api/estop", () => apiMocks);

import EstopTab from "./EstopTab";

function renderTab() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EstopTab />
    </QueryClientProvider>,
  );
}

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

describe("EstopTab query states", () => {
  it("keeps controls hidden while the emergency state is loading", () => {
    apiMocks.getEstop.mockReturnValue(new Promise(() => {}));

    renderTab();

    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
  });

  it("shows a retryable failure instead of a false safe state", async () => {
    apiMocks.getEstop.mockRejectedValue({
      status: 503,
      code: "estop-unavailable",
      message: "emergency service offline",
      correlationId: "corr-estop",
      retryable: true,
    });

    renderTab();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("emergency service offline");
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(apiMocks.getEstop).toHaveBeenCalledTimes(2);
  });
});
