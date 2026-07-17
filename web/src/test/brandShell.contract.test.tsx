// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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

const BRAND_PATH = resolve(process.cwd(), "public/brand.png");
const API_CONTRACT_PATH = resolve(process.cwd(), "src/contracts/api.ts");
const BRAND_SHA256 = "3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799";
const TAGLINE = "成功只有一个——按照自己的方式，去度过人生。";
const HEADER_STATUS_LABELS = ["彩蛋", "通用", "English", "实时", "通政"];
const DEPARTMENT_STRUCTURE = [
  { group: "敕令", departments: ["御书房", "文书房"] },
  { group: "政要", departments: ["内阁", "廷议", "都察院", "权印司"] },
  { group: "百官", departments: ["百官阁", "文渊阁", "位面", "考成"] },
  { group: "外朝", departments: ["藏兵阁", "鸿胪寺", "通政司", "户部账房"] },
];

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

function renderedDepartmentStructure(container: HTMLElement) {
  const menu = container.querySelector(".ant-menu-root");
  const groups = Array.from(menu?.children ?? []).filter((node) =>
    node.classList.contains("ant-menu-item-group"),
  );

  return groups.map((group) => ({
    group: group.querySelector(".ant-menu-item-group-title")?.textContent?.trim() ?? "",
    departments: Array.from(group.querySelectorAll(".ant-menu-title-content")).map(
      (item) => item.textContent?.trim() ?? "",
    ),
  }));
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

  it("freezes the exact brand asset, quote, and five right-side labels", () => {
    const digest = createHash("sha256").update(readFileSync(BRAND_PATH)).digest("hex");
    const { container } = render(
      <MemoryRouter>
        <AppHeader isWsConnected />
      </MemoryRouter>,
    );

    expect(digest).toBe(BRAND_SHA256);
    expect(container.querySelector("img")).toHaveAttribute("src", "/brand.png");
    expect(screen.getByText(TAGLINE)).toBeInTheDocument();
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

  it("keeps the brand quote and five visible labels frozen after switching locale", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <HeaderLocaleHarness />
      </MemoryRouter>,
    );

    await user.click(screen.getByText("English"));
    const frozenInEnglish =
      screen.queryByText("天枢") !== null &&
      screen.queryByText(TAGLINE) !== null &&
      HEADER_STATUS_LABELS.every((label) => screen.queryByText(label) !== null);
    await user.click(screen.getByRole("button", { name: "reset-locale" }));

    expect(frozenInEnglish).toBe(true);
  });

  it("freezes the exact ordered groups and departments outside the Control item", async () => {
    const user = userEvent.setup();
    const toggleTheme = vi.fn();
    const { container } = render(
      <MemoryRouter>
        <ThemeContext.Provider value={{ mode: "dark", toggleTheme }}>
          <AppSidebar />
        </ThemeContext.Provider>
      </MemoryRouter>,
    );

    const renderedStructure = renderedDepartmentStructure(container);
    const rootItems = Array.from(container.querySelector(".ant-menu-root")?.children ?? []).filter(
      (node) => node.classList.contains("ant-menu-item"),
    );
    expect(rootItems.map((item) => item.textContent?.trim())).toEqual(["中枢总览", "演化中心"]);
    expect(renderedStructure).toEqual(DEPARTMENT_STRUCTURE);
    expect(renderedStructure).toHaveLength(4);
    expect(renderedStructure.flatMap(({ departments }) => departments)).toHaveLength(14);

    await user.click(screen.getByRole("button", { name: "切换浅色" }));
    expect(toggleTheme).toHaveBeenCalledOnce();

    const sider = container.querySelector(".ant-layout-sider");
    expect(sider).not.toHaveClass("ant-layout-sider-collapsed");
    await user.click(screen.getByRole("button", { name: /收起侧栏/ }));
    expect(sider).toHaveClass("ant-layout-sider-collapsed");
    expect(screen.getByRole("button", { name: "切换浅色" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "中枢总览" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "演化中心" })).toBeInTheDocument();
  });

  it("restores the persisted collapsed sidebar without losing controls", async () => {
    const user = userEvent.setup();
    const renderSidebar = () =>
      render(
        <MemoryRouter>
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
  });

  it("uses 裁决 for Chinese governance and rejects historical alternatives", () => {
    for (const locale of [zhClassic, zhModern]) {
      expect(locale.entity.decree).toBe("裁决");
      expect(locale.action.review).toBe("裁决");
      expect(JSON.stringify(locale)).not.toMatch(/批红|朱批|司礼监代批/);
    }
  });
});
