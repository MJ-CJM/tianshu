// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GovernanceContractPreview } from "../../api/types";

const edictsApi = vi.hoisted(() => ({
  previewEdictGovernance: vi.fn(),
}));

vi.mock("../../api/edicts", () => ({
  parseEdict: vi.fn(),
  previewEdictGovernance: edictsApi.previewEdictGovernance,
}));
vi.mock("../../hooks/usePersonas", () => ({
  usePersonas: () => ({ data: [] }),
}));
vi.mock("../policy/PolicyProfilePanel", () => ({ default: () => null }));
vi.mock("./NetworkCapabilitySection", () => ({ default: () => null }));
vi.mock("./AcceptanceConfigSection", () => ({ default: () => null }));

import EdictForm from "./EdictForm";

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

function compatiblePreview(): GovernanceContractPreview {
  return {
    compatible: true,
    requested_contract: {},
    requested_contract_hash: "a".repeat(64),
    effective_contract: {
      requested_contract_hash: "a".repeat(64),
      executor: { adapter_id: "native" },
      executor_manifest_id: "tianshu.native.v1",
      executor_manifest_version: "1",
      runtime_probe_id: "probe-1",
      effective_controls: [],
      unsupported_advisory: [],
    },
    mandatory_mismatches: [],
    execution_mode: "single",
    execution_mode_mismatches: [],
    advisory_gaps: [],
    executor_level: "contained",
    experimental: false,
    manifest_hash: "b".repeat(64),
    runtime_probe_id: "probe-1",
  };
}

beforeEach(() => {
  edictsApi.previewEdictGovernance.mockResolvedValue(compatiblePreview());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EdictForm UX defaults", () => {
  it("shows the task type before the goal and keeps quick selected by default", () => {
    render(<EdictForm onSubmit={vi.fn()} loading={false} />);

    const quickPreset = screen.getByRole("radio", { name: /速办/ });
    const goal = screen.getByLabelText("敕令旨意");

    expect(quickPreset).toBeChecked();
    expect(
      quickPreset.compareDocumentPosition(goal) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("really selects quick and submits its safe on-failure review default", async () => {
    const onSubmit = vi.fn();
    render(<EdictForm onSubmit={onSubmit} loading={false} />);

    expect(screen.getByRole("radio", { name: /速办/ })).toBeChecked();
    fireEvent.change(screen.getByLabelText("敕令旨意"), {
      target: { value: "快速处理这项任务" },
    });
    fireEvent.click(screen.getByRole("button", { name: /颁发敕令$/ }));

    await waitFor(() => expect(edictsApi.previewEdictGovernance).toHaveBeenCalledOnce());
    expect(edictsApi.previewEdictGovernance.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ review_policy: "on_failure" }),
    );
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
  });

  it("lets a keyboard user choose a task preset through radio semantics", async () => {
    const user = userEvent.setup();
    render(<EdictForm onSubmit={vi.fn()} loading={false} />);

    const analysis = screen.getByRole("radio", { name: /案陈/ });
    analysis.focus();
    await user.keyboard("[Space]");

    expect(analysis).toBeChecked();
    expect(screen.getByRole("radio", { name: /速办/ })).not.toBeChecked();
  });

  it("uses the requested scheduled mode instead of immediate execution", () => {
    render(
      <EdictForm
        onSubmit={vi.fn()}
        loading={false}
        initialScheduleMode="once"
      />,
    );

    expect(screen.getByRole("radio", { name: "定时执行" })).toBeChecked();
    expect(screen.getByLabelText("施行时刻")).toBeInTheDocument();
  });

  it("converges a recurring schedule to once and disables recurrence for long tasks", async () => {
    render(<EdictForm onSubmit={vi.fn()} loading={false} />);

    const recurring = screen.getByRole("radio", { name: /重复执行|循期执行/ });
    fireEvent.click(recurring);
    expect(recurring).toBeChecked();

    fireEvent.click(screen.getByRole("radio", { name: /案陈/ }));

    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "定时执行" })).toBeChecked(),
    );
    expect(recurring).toBeDisabled();
    expect(screen.getByText(/长任务不支持重复执行|长任务不可循期执行/)).toBeInTheDocument();
  });

  it("does not revive research-only settings after switching presets", async () => {
    const onSubmit = vi.fn();
    render(<EdictForm onSubmit={onSubmit} loading={false} />);

    fireEvent.click(screen.getByRole("radio", { name: /穷究/ }));
    fireEvent.click(screen.getByRole("radio", { name: /速办/ }));
    fireEvent.click(screen.getByRole("radio", { name: /案陈/ }));
    fireEvent.change(screen.getByLabelText("敕令旨意"), {
      target: { value: "分析当前项目" },
    });
    fireEvent.click(screen.getByRole("button", { name: /颁发敕令$/ }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    const submitted = onSubmit.mock.calls[0]?.[0];
    expect(submitted?.execution_profile).toBe("checkpointed");
    expect(submitted?.acceptance).toEqual({});
  });
});
