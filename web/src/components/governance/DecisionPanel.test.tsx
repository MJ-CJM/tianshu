// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DecisionPanel from "./DecisionPanel";

afterEach(cleanup);

describe("DecisionPanel", () => {
  it("requires a non-empty trimmed reason", async () => {
    const onSubmit = vi.fn();
    render(
      <DecisionPanel
        decision={{ id: "decision-1", status: "pending", version: 4 }}
        onSubmit={onSubmit}
      />,
    );

    await userEvent.type(screen.getByLabelText("裁决理由"), "   ");
    await userEvent.click(screen.getByRole("button", { name: "提交裁决" }));

    expect(screen.getByRole("alert")).toHaveTextContent("必须填写裁决理由");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits reason and expected version, then locks only on a durable resolution", async () => {
    const onSubmit = vi.fn(async () => ({
      status: "resolved" as const,
      action: "approve" as const,
      reason: "风险已核验",
      version: 5,
    }));
    render(
      <DecisionPanel
        decision={{ id: "decision-1", status: "pending", version: 4 }}
        onSubmit={onSubmit}
      />,
    );

    await userEvent.type(screen.getByLabelText("裁决理由"), " 风险已核验 ");
    await userEvent.click(screen.getByRole("button", { name: "提交裁决" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        decisionRequestId: "decision-1",
        action: "approve",
        reason: "风险已核验",
        expectedVersion: 4,
      }),
    );
    expect(screen.getByText("已裁决")).toBeInTheDocument();
    expect(screen.getByLabelText("裁决理由")).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交裁决" })).toBeDisabled();
  });

  it("keeps an already resolved decision immutable", () => {
    render(
      <DecisionPanel
        decision={{
          id: "decision-2",
          status: "resolved",
          version: 8,
          resolvedAction: "reject",
          resolvedReason: "证据不足",
        }}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText("已裁决")).toBeInTheDocument();
    expect(screen.getByLabelText("裁决理由")).toBeDisabled();
  });
});
