// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/study/AllEdictsView", () => ({
  default: () => <div>all tasks</div>,
}));

import RoyalStudyPage from "./RoyalStudyPage";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

afterEach(cleanup);

describe("Royal Study task workspace", () => {
  it("shows the complete task workspace by default", () => {
    render(
      <MemoryRouter initialEntries={["/approvals"]}>
        <RoyalStudyPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "御书房" })).toBeInTheDocument();
    expect(screen.getByText("all tasks")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /颁发敕令/ }),
    ).toBeInTheDocument();
  });

  it("keeps the old all-tab bookmark inside the workspace", () => {
    render(
      <MemoryRouter initialEntries={["/approvals?tab=all"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <LocationProbe />
                <RoyalStudyPage />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("/approvals")).toBeInTheDocument();
    expect(screen.getByText("all tasks")).toBeInTheDocument();
  });
});
