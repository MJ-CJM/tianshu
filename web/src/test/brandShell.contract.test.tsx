// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import type { ApiProblem, PageDataStatus } from "../contracts/api";
import { ThemeContext } from "../hooks/useTheme";
import { LocaleContext, useLocaleProvider } from "../hooks/useLocale";
import zhClassic from "../i18n/locales/zh-classic.json";
import zhModern from "../i18n/locales/zh-modern.json";

const health = vi.hoisted(() => ({
  value: {
    data: { status: "ready", profile: "live" },
    isError: false,
  },
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    mode: "trusted-local",
    principal: null,
    logout: vi.fn(),
  }),
}));
vi.mock("../hooks/useApprovals", () => ({
  useNeedsReview: () => ({ data: { data: [], metadata: { total: 0 } } }),
}));
vi.mock("../hooks/useHealth", () => ({
  useHealth: () => health.value,
}));

import AppHeader from "../components/layout/AppHeader";
import AppSidebar from "../components/layout/AppSidebar";

function HeaderLocaleHarness() {
  const locale = useLocaleProvider();
  return (
    <LocaleContext.Provider value={locale}>
      <AppHeader isWsConnected />
      <button type="button" onClick={() => locale.setLocale("zh-classic")}>
        reset-locale
      </button>
    </LocaleContext.Provider>
  );
}

const API_CONTRACT_PATH = resolve(process.cwd(), "src/contracts/api.ts");
const HEADER_STATUS_LABELS = ["彩蛋", "通用", "English", "实时", "通政"];
const PRIMARY_NAVIGATION = ["中枢", "御书房", "朝堂", "百司", "天工院实验", "内府"];

function SidebarRouteHarness() {
  const navigate = useNavigate();
  return (
    <>
      <AppSidebar />
      <button type="button" onClick={() => navigate("/memory")}>
        go-to-memory
      </button>
    </>
  );
}

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
  };
}

beforeEach(() => vi.stubGlobal("localStorage", memoryStorage()));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  health.value = {
    data: { status: "ready", profile: "live" },
    isError: false,
  };
});

describe("S4 desktop brand shell contract", () => {
  it("freezes the shared page-data and API problem types", () => {
    readFileSync(API_CONTRACT_PATH, "utf8");

    expectTypeOf<PageDataStatus>().toEqualTypeOf<
      | "loading"
      | "success-empty"
      | "success-data"
      | "stale"
      | "error"
      | "permission-denied"
      | "service-unavailable"
    >();
    expectTypeOf<ApiProblem>().toEqualTypeOf<{
      status: number;
      code: string;
      message: string;
      correlationId: string | null;
      retryable: boolean;
    }>();
  });

  it("keeps the brand asset and five right-side labels", () => {
    const { container } = render(
      <MemoryRouter>
        <AppHeader isWsConnected />
      </MemoryRouter>,
    );

    expect(container.querySelector("img")).toHaveAttribute("src", "/brand.png");
    for (const label of HEADER_STATUS_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders the localized degraded state from the real health indicator", () => {
    health.value = {
      data: { status: "degraded", profile: "live" },
      isError: false,
    };

    render(
      <MemoryRouter>
        <AppHeader isWsConnected />
      </MemoryRouter>,
    );

    expect(screen.getByText("通政")).toBeInTheDocument();
    expect(screen.getByText("通政").closest("[role='status']")).toHaveAttribute(
      "aria-label",
      "通政(降)",
    );
  });

  it("keeps the brand name and five visible labels frozen after switching locale", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <HeaderLocaleHarness />
      </MemoryRouter>,
    );

    await user.click(screen.getByText("English"));
    const frozenInEnglish =
      screen.queryByText("天枢") !== null &&
      HEADER_STATUS_LABELS.every((label) => screen.queryByText(label) !== null);
    await user.click(screen.getByRole("button", { name: "reset-locale" }));

    expect(frozenInEnglish).toBe(true);
  });

  it("keeps six primary destinations in a two-level accordion", async () => {
    const user = userEvent.setup();
    const toggleTheme = vi.fn();
    const { container } = render(
      <MemoryRouter initialEntries={["/approvals"]}>
        <ThemeContext.Provider value={{ mode: "dark", toggleTheme }}>
          <SidebarRouteHarness />
        </ThemeContext.Provider>
      </MemoryRouter>,
    );

    const rootItems = Array.from(container.querySelector(".ant-menu-root")?.children ?? []);
    expect(rootItems.map((item) => {
      const ownTitle = item.matches(".ant-menu-submenu")
        ? item.querySelector(":scope > .ant-menu-submenu-title")
        : item;
      return ownTitle?.textContent?.trim() ?? "";
    })).toEqual(PRIMARY_NAVIGATION);
    expect(rootItems[1]).toHaveClass("ant-menu-submenu-open");
    expect(rootItems[2]).not.toHaveClass("ant-menu-submenu-open");
    expect(rootItems[3]).not.toHaveClass("ant-menu-submenu-open");
    expect(rootItems[4]).not.toHaveClass("ant-menu-submenu-open");
    expect(rootItems[5]).not.toHaveClass("ant-menu-submenu-open");
    expect(screen.getByRole("menuitem", { name: "全部敕令" })).toBeInTheDocument();
    expect(screen.queryByText("正式能力")).not.toBeInTheDocument();
    expect(screen.queryByText("实验室")).not.toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "朝堂" }));
    // 展开一个不再收起其他：收不收由用户决定（issue #71）
    expect(rootItems[1]).toHaveClass("ant-menu-submenu-open");
    expect(rootItems[2]).toHaveClass("ant-menu-submenu-open");
    expect(screen.getByRole("menuitem", { name: "吏部" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "廷议" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "内阁" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "go-to-memory" }));
    // 跳转只「并入」目标分组，不清掉用户已展开的（issue #71）
    expect(rootItems[2]).toHaveClass("ant-menu-submenu-open");
    expect(rootItems[3]).toHaveClass("ant-menu-submenu-open");
    expect(screen.getByRole("menuitem", { name: "翰林院" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "鸿胪寺" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "通政司" })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: /天工院.*实验/ }));
    expect(rootItems[3]).toHaveClass("ant-menu-submenu-open");
    expect(rootItems[4]).toHaveClass("ant-menu-submenu-open");
    expect(screen.getByRole("menuitem", { name: /演化司.*实验/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /诸界台.*实验/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /考功司.*试行/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /客卿馆.*实验/ })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "内府" }));
    expect(rootItems[4]).toHaveClass("ant-menu-submenu-open");
    expect(rootItems[5]).toHaveClass("ant-menu-submenu-open");
    expect(screen.getByRole("menuitem", { name: "藏兵阁" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "权印司" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "户部账房" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换浅色" }));
    expect(toggleTheme).toHaveBeenCalledOnce();

    const sider = container.querySelector(".ant-layout-sider");
    expect(sider).not.toHaveClass("ant-layout-sider-collapsed");
    await user.click(screen.getByRole("button", { name: /收起侧栏/ }));
    expect(sider).toHaveClass("ant-layout-sider-collapsed");
    expect(screen.getByRole("button", { name: "切换浅色" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "中枢" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "御书房" })).toBeInTheDocument();
    expect(container.querySelectorAll(".ant-menu-submenu-open")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "展开" }));
    expect(sider).not.toHaveClass("ant-layout-sider-collapsed");
    expect(rootItems[3]).toHaveClass("ant-menu-submenu-open");
  });

  it("restores the persisted collapsed sidebar without losing controls", async () => {
    const user = userEvent.setup();
    const renderSidebar = () =>
      render(
        <MemoryRouter initialEntries={["/personas"]}>
          <ThemeContext.Provider value={{ mode: "light", toggleTheme: vi.fn() }}>
            <AppSidebar />
          </ThemeContext.Provider>
        </MemoryRouter>,
      );

    const first = renderSidebar();
    await user.click(screen.getByRole("button", { name: /收起侧栏/ }));
    first.unmount();

    const second = renderSidebar();
    expect(second.container.querySelector(".ant-layout-sider")).toHaveClass(
      "ant-layout-sider-collapsed",
    );
    expect(screen.getByRole("button", { name: "切换深色" })).toBeInTheDocument();
    expect(second.container.querySelectorAll(".ant-menu-submenu-open")).toHaveLength(0);
  });

  it("uses 裁决 for Chinese governance and rejects historical alternatives", () => {
    for (const locale of [zhClassic, zhModern]) {
      expect(locale.entity.decree).toBe("裁决");
      expect(locale.action.review).toBe("裁决");
      expect(JSON.stringify(locale)).not.toMatch(/批红|朱批|司礼监代批/);
    }
  });
});
