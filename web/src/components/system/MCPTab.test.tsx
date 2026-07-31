// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  useMCPServers: vi.fn(),
  usePatchMCPServer: vi.fn(),
  useReloadMCP: vi.fn(),
}));

vi.mock("../../hooks/useMCP", () => hookMocks);
vi.mock("./CreateMCPServerModal", () => ({ default: () => null }));

import MCPTab from "./MCPTab";

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

describe("MCPTab query states", () => {
  it("shows a retryable failure instead of an empty server list", async () => {
    const refetch = vi.fn();
    hookMocks.useMCPServers.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: {
        status: 503,
        code: "mcp-unavailable",
        message: "MCP registry offline",
        correlationId: "corr-mcp",
        retryable: true,
      },
      refetch,
    });
    hookMocks.useReloadMCP.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    hookMocks.usePatchMCPServer.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });

    render(<MCPTab />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("MCP registry offline");
    expect(screen.queryByText("尚无 MCP 服务器")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(refetch).toHaveBeenCalledOnce();
  });
});
