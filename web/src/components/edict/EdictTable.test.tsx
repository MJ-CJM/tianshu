// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Edict, Memorial } from "../../api/types";
import EdictTable from "./EdictTable";

vi.mock("../../i18n", () => ({
  useT: () => (key: string) => key,
}));

const baseEdict = {
  id: "edict-immediate",
  title: "Immediate conversation",
  goal: "Continue the work",
  context: null,
  status: "open",
  created_at: "2026-07-31T08:00:00Z",
  priority: "normal",
  review_policy: "never",
  schedule: {
    type: "immediate",
    at: null,
    cron: null,
    timezone: "Asia/Shanghai",
  },
  runtime: {
    timeout_seconds: 300,
    max_iterations: 1,
    max_concurrency: 1,
    retry_limit: 0,
    token_budget: null,
    cost_budget_cny: null,
    approval_required_tools: [],
    lifecycle_phase: "active",
    conversation: true,
    executor: "native",
  },
  constraints: [],
  output_format: null,
  source: "web",
  submitter: null,
  execution_profile: "foreground",
} as Edict;

const completedMemorial = {
  status: "completed",
  review_status: "not_required",
} as Memorial;

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
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
  vi.restoreAllMocks();
});

describe("EdictTable workspace columns", () => {
  it("shows overlapping task types and the real execution phase", () => {
    const scheduledLongTask = {
      ...baseEdict,
      id: "edict-scheduled",
      title: "Scheduled long task",
      schedule: { ...baseEdict.schedule, type: "once" },
      execution_profile: "checkpointed",
      runtime: {
        ...baseEdict.runtime,
        executor: "keqing:codex",
      },
    } as Edict;

    render(
      <MemoryRouter>
        <EdictTable
          edicts={[baseEdict, scheduledLongTask]}
          total={2}
          page={1}
          pageSize={20}
          loading={false}
          onPageChange={vi.fn()}
          onDelete={vi.fn()}
          latestMemorials={{ [baseEdict.id]: completedMemorial }}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("columnheader", { name: "comp.edictTable.taskType" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "comp.edictTable.progress" }),
    ).toBeInTheDocument();
    expect(screen.getByText("taskKind.immediate")).toBeInTheDocument();
    expect(screen.getAllByText("taskKind.conversation")).toHaveLength(2);
    expect(screen.getByText("taskKind.scheduledOnce")).toBeInTheDocument();
    expect(screen.getByText("taskKind.longRunning")).toBeInTheDocument();
    expect(screen.getByText("taskKind.keqing")).toBeInTheDocument();
    expect(screen.getByText("phase.idle")).toBeInTheDocument();
    expect(screen.getByText("status.scheduled")).toBeInTheDocument();
  });

  it("does not present a missing progress response as a not-started task", () => {
    render(
      <MemoryRouter>
        <EdictTable
          edicts={[baseEdict]}
          total={1}
          page={1}
          pageSize={20}
          loading={false}
          onPageChange={vi.fn()}
          onDelete={vi.fn()}
          progressUnavailable
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("phase.unavailable")).toBeInTheDocument();
    expect(screen.queryByText("phase.no_memorial")).not.toBeInTheDocument();
  });
});
