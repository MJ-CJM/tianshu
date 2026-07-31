// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import TaskListPage from "./TaskListPage";

afterEach(cleanup);

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

describe("Legacy task list route", () => {
  it("redirects to the unified Royal Study workspace", () => {
    render(
      <MemoryRouter initialEntries={["/edicts"]}>
        <Routes>
          <Route path="/edicts" element={<TaskListPage />} />
          <Route path="/approvals" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("/approvals")).toBeInTheDocument();
  });
});
