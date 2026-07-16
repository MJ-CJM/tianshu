// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import type { ApiProblem, PageDataStatus } from "../contracts/api";
import { ThemeContext } from "../hooks/useTheme";
import zhClassic from "../i18n/locales/zh-classic.json";
import zhModern from "../i18n/locales/zh-modern.json";

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
vi.mock("../components/common/HealthDot", () => ({
  default: () => <span>通政</span>,
}));

import AppHeader from "../components/layout/AppHeader";
import AppSidebar from "../components/layout/AppSidebar";

const BRAND_PATH = resolve(process.cwd(), "public/brand.png");
const API_CONTRACT_PATH = resolve(process.cwd(), "src/contracts/api.ts");
const BRAND_SHA256 = "3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799";
const TAGLINE = "成功只有一个——按照自己的方式，去度过人生。";
const HEADER_STATUS_LABELS = ["彩蛋", "通用", "English", "实时", "通政"];
const DEPARTMENT_GROUPS = ["敕令", "政要", "百官", "外朝"];
const DEPARTMENTS = [
  "御书房",
  "文书房",
  "内阁",
  "廷议",
  "都察院",
  "权印司",
  "百官阁",
  "文渊阁",
  "位面",
  "考成",
  "藏兵阁",
  "鸿胪寺",
  "通政司",
  "户部账房",
];

afterEach(cleanup);

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
    const { container } = render(<AppHeader isWsConnected />);

    expect(digest).toBe(BRAND_SHA256);
    expect(container.querySelector("img")).toHaveAttribute("src", "/brand.png");
    expect(screen.getByText(TAGLINE)).toBeInTheDocument();
    for (const label of HEADER_STATUS_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("freezes four groups, fourteen departments, light mode, and sidebar collapse", async () => {
    const user = userEvent.setup();
    const toggleTheme = vi.fn();
    const { container } = render(
      <MemoryRouter>
        <ThemeContext.Provider value={{ mode: "dark", toggleTheme }}>
          <AppSidebar />
        </ThemeContext.Provider>
      </MemoryRouter>,
    );

    for (const group of DEPARTMENT_GROUPS) {
      expect(screen.getByText(group)).toBeInTheDocument();
    }
    for (const department of DEPARTMENTS) {
      expect(screen.getByText(department)).toBeInTheDocument();
    }

    await user.click(screen.getByRole("button", { name: /浅色模式/ }));
    expect(toggleTheme).toHaveBeenCalledOnce();

    const sider = container.querySelector(".ant-layout-sider");
    expect(sider).not.toHaveClass("ant-layout-sider-collapsed");
    await user.click(screen.getByRole("button", { name: /收起侧栏/ }));
    expect(sider).toHaveClass("ant-layout-sider-collapsed");
  });

  it("uses 裁决 for Chinese governance and rejects historical alternatives", () => {
    for (const locale of [zhClassic, zhModern]) {
      expect(locale.entity.decree).toBe("裁决");
      expect(locale.action.review).toBe("裁决");
      expect(JSON.stringify(locale)).not.toMatch(/批红|朱批|司礼监代批/);
    }
  });
});
