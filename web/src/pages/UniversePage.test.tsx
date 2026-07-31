// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  archiveUniverse: vi.fn(),
  branchUniverse: vi.fn(),
  deleteUniverse: vi.fn(),
  enableParallelUniverse: vi.fn(),
  getCodeDiff: vi.fn(),
  getUniverseStatus: vi.fn(),
  listEvalRuns: vi.fn(),
  listUniverses: vi.fn(),
  proposeAutoCode: vi.fn(),
  proposeCodeVariant: vi.fn(),
  restoreUniverse: vi.fn(),
  triggerEvolve: vi.fn(),
}));

vi.mock("../api/universe", () => apiMocks);

import UniversePage from "./UniversePage";

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
  apiMocks.getUniverseStatus.mockResolvedValue({
    success: true,
    data: { enabled: true },
  });
  apiMocks.listUniverses.mockResolvedValue({
    success: true,
    data: [
      {
        id: "config-candidate",
        name: "配置候选",
        status: "challenger",
        origin: "mutation",
        parent_universe_id: null,
        code_ref: null,
        fitness: null,
        created_at: "2026-07-31T00:00:00Z",
      },
      {
        id: "code-candidate",
        name: "代码候选",
        status: "challenger",
        origin: "code_variant",
        parent_universe_id: null,
        code_ref: "refs/candidate",
        fitness: null,
        created_at: "2026-07-31T00:00:00Z",
      },
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("UniversePage capability truth", () => {
  it("keeps unavailable runtime switch and promotion actions out of the UI", async () => {
    render(<UniversePage />);

    expect(await screen.findByText("配置候选")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "位面" })).toBeInTheDocument();
    expect(screen.getAllByText("实验")).toHaveLength(2);
    expect(
      screen.getByText("创建、分支、生成代码候选、查看差异、考成、归档与恢复位面。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("不可从 Web 切换在役运行时或晋升代码；候选不会自行写入主工房。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "切换/回滚" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "晋升代码" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "代码演化" }));
    expect(screen.getByPlaceholderText(/具体 \.py 文件/)).toHaveValue(
      "src/tianshu/persona/selector.py",
    );
  });
});
