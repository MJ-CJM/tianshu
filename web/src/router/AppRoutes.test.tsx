// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { lazy, Suspense } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, useLocation, useNavigationType } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/layout/AppLayout", () => ({ default: () => <Outlet /> }));
vi.mock("../pages/RoyalStudyPage", () => ({ default: () => <h1>御书房</h1> }));

import AppRoutes, { RouteErrorBoundary } from "./AppRoutes";

function NavigationProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return <output>{`${location.pathname}:${navigationType}`}</output>;
}

function suppressExpectedBoundaryError(event: ErrorEvent) {
  event.preventDefault();
}

beforeEach(() => window.addEventListener("error", suppressExpectedBoundaryError));
afterEach(() => {
  window.removeEventListener("error", suppressExpectedBoundaryError);
  cleanup();
  vi.restoreAllMocks();
});

describe("desktop application routes", () => {
  it("replaces the root entry with the canonical control route", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <NavigationProbe />
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByText("/control:REPLACE")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "中枢总览" })).toBeInTheDocument();
  });

  it("keeps approvals as the canonical Royal Study route", async () => {
    render(
      <MemoryRouter initialEntries={["/approvals"]}>
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "御书房" })).toBeInTheDocument();
  });

  it("loads every page module through a route-level lazy boundary", () => {
    const source = readFileSync(resolve(process.cwd(), "src/router/AppRoutes.tsx"), "utf8");
    const pageModules = [
      "ControlCenterPage",
      "RoyalStudyPage",
      "EdictCreatePage",
      "EdictDetailPage",
      "SchedulerPage",
      "AuditDashboardPage",
      "CostDashboardPage",
      "MemoryDashboardPage",
      "ConsultationPage",
      "CabinetPage",
      "HongluisiPage",
      "TongzhengPage",
      "PersonaDashboardPage",
      "PersonaDetailPage",
      "SystemManagementPage",
      "SessionRulesPage",
      "UniversePage",
      "EvalsPage",
      "DagBattleMapPage",
    ];

    expect(source).not.toMatch(/import\s+\w+\s+from\s+["']\.\.\/pages\//);
    for (const page of pageModules) {
      expect(source).toContain(`lazy(() => import("../pages/${page}"))`);
    }
    expect(source).toContain("<Suspense");
  });

  it("turns a rejected dynamic import into a retryable service state", async () => {
    const onRetry = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const privateChunkUrl =
      "https://cdn.example/private/chunk.js?token=chunk-secret";
    const FailedChunk = lazy(() =>
      Promise.reject(
        new TypeError(`Failed to fetch dynamically imported module: ${privateChunkUrl}`),
      ),
    );

    render(
      <RouteErrorBoundary onRetry={onRetry}>
        <Suspense fallback={<div>loading chunk</div>}>
          <FailedChunk />
        </Suspense>
      </RouteErrorBoundary>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("页面资源加载失败");
    expect(screen.getByRole("alert")).not.toHaveTextContent(privateChunkUrl);
    expect(screen.getByRole("alert")).not.toHaveTextContent("chunk-secret");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
    consoleError.mockRestore();
  });

  it("masks an unexpected render failure and logs the original error internally", async () => {
    const onRetry = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const secret = "postgres://admin:super-secret@db.internal/prod";
    function BrokenPage(): never {
      throw new Error(`page render exploded: ${secret}`);
    }

    render(
      <RouteErrorBoundary onRetry={onRetry}>
        <BrokenPage />
      </RouteErrorBoundary>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("请求失败");
    expect(screen.getByRole("alert")).toHaveTextContent("页面发生异常，请重试");
    expect(screen.getByRole("alert")).not.toHaveTextContent("page render exploded");
    expect(screen.getByRole("alert")).not.toHaveTextContent(secret);
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalledWith(
      "Route render failed",
      expect.objectContaining({ message: expect.stringContaining(secret) }),
      expect.objectContaining({ componentStack: expect.any(String) }),
    );
    consoleError.mockRestore();
  });
});
