// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listInstances: vi.fn(),
  listPersonas: vi.fn(),
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  setInstanceEnabled: vi.fn(),
  deleteInstance: vi.fn(),
}));

vi.mock("../../api/tongzheng", () => apiMocks);

import InstanceManager from "./InstanceManager";

const serviceError = {
  status: 503,
  code: "service-unavailable",
  message: "通政司数据暂不可用",
  correlationId: "tongzheng-test",
  retryable: true,
};

function renderManager() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InstanceManager />
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

describe("InstanceManager data truth", () => {
  it("shows a persistent error instead of an empty instance table", async () => {
    apiMocks.listInstances.mockRejectedValue(serviceError);
    apiMocks.listPersonas.mockResolvedValue([]);

    renderManager();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "通政司数据暂不可用",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("keeps loaded instances visible when only persona choices fail", async () => {
    apiMocks.listInstances.mockResolvedValue([
      {
        instance_id: "feishu-office",
        channel_type: "feishu",
        label: "办公飞书",
        running: false,
        enabled: true,
      },
    ]);
    apiMocks.listPersonas.mockRejectedValue(serviceError);

    renderManager();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "通政司数据暂不可用",
    );
    expect(screen.getByText("办公飞书")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
