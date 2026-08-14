// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeContext } from "../../hooks/useTheme";
import AppSidebar from "./AppSidebar";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ mode: "trusted-local", principal: null, logout: vi.fn() }),
}));
vi.mock("../../hooks/useApprovals", () => ({
  useNeedsReview: () => ({ data: { metadata: { total: 0 }, data: [] } }),
}));
vi.mock("../../hooks/useHealth", () => ({
  useHealth: () => ({ data: { data: { status: "ready", profile: "live" } }, isError: false }),
}));

function renderSidebar(initial = "/") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <ThemeContext.Provider value={{ mode: "light", toggleTheme: vi.fn() }}>
        <Routes>
          <Route path="*" element={<AppSidebar />} />
        </Routes>
      </ThemeContext.Provider>
    </MemoryRouter>,
  );
}

/** 当前展开的分组标题 */
function openSectionTitles(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".ant-menu-submenu-open")].map(
    (el) => el.querySelector(".ant-menu-title-content")?.textContent?.trim() ?? "",
  );
}

/** jsdom 的 localStorage 在本项目被禁用，照搬 brandShell 契约测试的内存替身 */
function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  } as Storage;
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("侧边栏分组展开", () => {
  it("展开一个分组不该收起其他分组", async () => {
    const user = userEvent.setup();
    const { container } = renderSidebar();

    await user.click(screen.getByText("御书房"));
    await user.click(screen.getByText("百司"));

    // 收不收由用户决定，别替他手风琴（issue #71）
    expect(openSectionTitles(container)).toEqual(
      expect.arrayContaining(["御书房", "百司"]),
    );
  });

  it("用户主动收起的分组要真的收起", async () => {
    const user = userEvent.setup();
    const { container } = renderSidebar();

    await user.click(screen.getByText("御书房"));
    expect(openSectionTitles(container)).toContain("御书房");

    await user.click(screen.getByText("御书房"));
    expect(openSectionTitles(container)).not.toContain("御书房");
  });

  it("进入某页面时其所属分组自动展开", () => {
    const { container } = renderSidebar("/hongluisi");
    expect(openSectionTitles(container)).toContain("百司");
  });

  it("跳转到别处后，手动展开的分组仍然开着", async () => {
    const user = userEvent.setup();

    function Jump() {
      const navigate = useNavigate();
      return <button onClick={() => navigate("/hongluisi")}>去鸿胪寺</button>;
    }

    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <ThemeContext.Provider value={{ mode: "light", toggleTheme: vi.fn() }}>
          <AppSidebar />
          <Jump />
        </ThemeContext.Provider>
      </MemoryRouter>,
    );

    await user.click(screen.getByText("御书房"));
    await user.click(screen.getByRole("button", { name: "去鸿胪寺" }));

    // 侧边栏就是跨页面导航用的，展开状态该撑过跳转
    const open = openSectionTitles(container);
    expect(open).toContain("御书房"); // 手动展开的还在
    expect(open).toContain("百司"); // 目标页所属分组也展开了
  });
});
