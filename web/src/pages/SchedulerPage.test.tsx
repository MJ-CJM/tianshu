// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  list: vi.fn(),
  cancel: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  run: vi.fn(),
  update: vi.fn(),
}));
const schedulerApi = vi.hoisted(() => ({
  listRuns: vi.fn(),
}));

vi.mock("../hooks/useScheduler", () => ({
  useSchedulerJobs: hooks.list,
  useCancelJob: () => ({ mutateAsync: hooks.cancel, isPending: false }),
  usePauseJob: () => ({ mutateAsync: hooks.pause, isPending: false }),
  useResumeJob: () => ({ mutateAsync: hooks.resume, isPending: false }),
  useRunJobNow: () => ({ mutateAsync: hooks.run, isPending: false }),
  useUpdateJob: () => ({ mutateAsync: hooks.update, isPending: false }),
}));

vi.mock("../api/scheduler", () => ({
  listSchedulerJobRuns: schedulerApi.listRuns,
}));

import SchedulerPage from "./SchedulerPage";

Object.defineProperty(window, "matchMedia", {
  writable: true,
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

const ACTIVE_JOB = {
  job_id: "job-1",
  edict_id: "edict-1",
  title: "每日巡检",
  schedule_type: "cron",
  status: "active",
  next_run: "2026-08-01T01:00:00Z",
  cron_expr: "0 9 * * *",
  interval_seconds: null,
  timezone: "Asia/Shanghai",
  last_run: null,
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function renderScheduler() {
  return render(
    <MemoryRouter initialEntries={["/scheduler"]}>
      <SchedulerPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  hooks.pause.mockResolvedValue({});
  hooks.resume.mockResolvedValue({});
  schedulerApi.listRuns.mockResolvedValue({ data: [] });
  hooks.list.mockReturnValue({
    data: [ACTIVE_JOB],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SchedulerPage", () => {
  it("shows the actual load failure instead of a fake empty list", () => {
    hooks.list.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("scheduler unavailable"),
      refetch: vi.fn(),
    });

    renderScheduler();

    expect(screen.getByRole("alert")).toHaveTextContent("scheduler unavailable");
    expect(screen.queryByText("暂无排期任务")).not.toBeInTheDocument();
  });

  it("lets the user pause an active schedule", async () => {
    renderScheduler();

    expect(screen.getByText("每日巡检")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /暂停$/ }));
    expect(hooks.pause).toHaveBeenCalledWith("job-1");
  });

  it("shows resume for a paused schedule", () => {
    hooks.list.mockReturnValue({
      data: [{ ...ACTIVE_JOB, status: "paused" }],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderScheduler();

    expect(screen.getByRole("button", { name: /恢复$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /暂停$/ })).not.toBeInTheDocument();
  });

  it("lets the user recover a failed schedule", async () => {
    hooks.list.mockReturnValue({
      data: [{ ...ACTIVE_JOB, status: "failed" }],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderScheduler();

    await userEvent.click(screen.getByRole("button", { name: /恢复$/ }));
    expect(hooks.resume).toHaveBeenCalledWith("job-1");
  });

  it("opens task creation with a scheduled default", async () => {
    const user = userEvent.setup();
    renderScheduler();

    await user.click(screen.getByRole("button", { name: /新建定时差事$/ }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/edicts/create?schedule=once",
    );
  });

  it("keeps fixed interval and custom cron inside advanced schedule options", async () => {
    const user = userEvent.setup();
    renderScheduler();

    await user.click(screen.getByRole("button", { name: "更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: /更改时刻$/ }));

    const dialog = await screen.findByRole("dialog", { name: "更改施行时刻" });
    expect(within(dialog).getByRole("radio", { name: "定时执行" })).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: "每日" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "每周" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: "固定间隔" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: "自定 Cron" })).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /高级排期$/ }));

    expect(within(dialog).getByRole("radio", { name: "固定间隔" })).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: "自定 Cron" })).toBeInTheDocument();
  });

  it("keeps a failed history load visible and lets the user retry", async () => {
    const user = userEvent.setup();
    schedulerApi.listRuns
      .mockRejectedValueOnce(new Error("history unavailable"))
      .mockResolvedValueOnce({
        data: [
          {
            id: "run-1",
            source: "scheduler",
            kind: "cron",
            status: "completed",
            edict_id: "edict-1",
            error: null,
            started_at: "2026-08-01T01:00:00Z",
            finished_at: "2026-08-01T01:01:00Z",
          },
        ],
      });
    renderScheduler();

    await user.click(screen.getByRole("button", { name: "更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: /施行记录$/ }));

    const dialog = await screen.findByRole("dialog", { name: "施行记录" });
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("未能载入施行记录");
    expect(alert).toHaveTextContent("history unavailable");
    expect(within(dialog).queryByRole("table")).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: /重\s*办/ }));

    await waitFor(() => expect(schedulerApi.listRuns).toHaveBeenCalledTimes(2));
    expect(await within(dialog).findByRole("table")).toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });
});
