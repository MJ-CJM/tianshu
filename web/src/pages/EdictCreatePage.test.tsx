// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/edict/EdictForm", () => ({
  default: ({
    initialScheduleMode,
  }: {
    initialScheduleMode?: "immediate" | "once" | "cron";
  }) => <output data-testid="schedule-mode">{initialScheduleMode}</output>,
}));

import { EdictCreationForm } from "./EdictCreatePage";

function renderCreationForm(entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <EdictCreationForm />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("Edict creation entry defaults", () => {
  it.each([
    ["/edicts/create?schedule=once", "once"],
    ["/edicts/create?schedule=cron", "cron"],
    ["/edicts/create", "immediate"],
    ["/edicts/create?schedule=unsupported", "immediate"],
  ])("maps %s to the %s form mode", (entry, expected) => {
    renderCreationForm(entry);

    expect(screen.getByTestId("schedule-mode")).toHaveTextContent(expected);
  });
});
