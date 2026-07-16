// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, useLocation, useNavigationType } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/layout/AppLayout", () => ({ default: () => <Outlet /> }));
vi.mock("../pages/RoyalStudyPage", () => ({ default: () => <h1>御书房</h1> }));

import AppRoutes from "./AppRoutes";

function NavigationProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return <output>{`${location.pathname}:${navigationType}`}</output>;
}

afterEach(cleanup);

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
});
