// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  useAgentConfig: vi.fn(),
  useUpdateAgentConfig: vi.fn(),
  // 「疆界」与本文件断言的 agent 参数无关，给足以渲染的默认值即可。
  useWorkspaceDir: vi.fn(() => ({ data: undefined })),
  useUpdateWorkspaceDir: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("../../hooks/useConfig", () => hookMocks);

import GlobalConfigTab from "./GlobalConfigTab";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GlobalConfigTab data truth", () => {
  it("does not render blank parameters when configuration loading fails", () => {
    hookMocks.useAgentConfig.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: {
        status: 503,
        code: "service-unavailable",
        message: "全局参数暂不可用",
        correlationId: "global-config-test",
        retryable: true,
      },
      refetch: vi.fn(),
    });
    hookMocks.useUpdateAgentConfig.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });

    render(<GlobalConfigTab />);

    expect(screen.getByRole("alert")).toHaveTextContent("全局参数暂不可用");
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });
});
