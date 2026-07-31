// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  useAgentConfig: vi.fn(),
  useConfigs: vi.fn(),
  useUpdateAgentConfig: vi.fn(),
}));

vi.mock("../../hooks/useConfig", () => hookMocks);

import TaskSlotsSection from "./TaskSlotsSection";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TaskSlotsSection data truth", () => {
  it("shows a retryable error when task-slot configuration is unavailable", () => {
    hookMocks.useAgentConfig.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: {
        status: 503,
        code: "service-unavailable",
        message: "任务槽位暂不可用",
        correlationId: "task-slots-test",
        retryable: true,
      },
      refetch: vi.fn(),
    });
    hookMocks.useConfigs.mockReturnValue({
      data: { configs: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    hookMocks.useUpdateAgentConfig.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });

    render(<TaskSlotsSection />);

    expect(screen.getByRole("alert")).toHaveTextContent("任务槽位暂不可用");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
