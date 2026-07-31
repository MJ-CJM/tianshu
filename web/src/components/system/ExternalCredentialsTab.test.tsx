// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listCredentials: vi.fn(),
  createCredential: vi.fn(),
  deleteCredential: vi.fn(),
  updateCredential: vi.fn(),
}));

vi.mock("../../api/credentials", () => apiMocks);

import ExternalCredentialsTab from "./ExternalCredentialsTab";

function renderTab() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <ExternalCredentialsTab />
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

describe("ExternalCredentialsTab loading", () => {
  it("loads once and keeps one persistent failure state across rerenders", async () => {
    apiMocks.listCredentials.mockRejectedValue({
      status: 503,
      code: "service-unavailable",
      message: "凭证服务暂不可用",
      correlationId: "credentials-test",
      retryable: true,
    });

    const user = userEvent.setup();
    renderTab();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "凭证服务暂不可用",
    );
    await user.click(screen.getByRole("button", { name: /新增|Add/ }));
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(apiMocks.listCredentials).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });
});
