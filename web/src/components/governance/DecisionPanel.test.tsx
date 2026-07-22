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
          resolvedBy: "auditor:independent",
          resolvedAt: "2026-07-17T09:00:00Z",
        }}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText("已裁决")).toBeInTheDocument();
    expect(screen.getByLabelText("裁决理由")).toBeDisabled();
    expect(screen.getByText("auditor:independent")).toBeInTheDocument();
  });

  it("shows a version conflict without overwriting the pending form and returns focus", async () => {
    const onConflict = vi.fn();
    const onSubmit = vi.fn(async () => {
      throw {
        status: 409,
        code: "decision_stale",
        message: "stale durable version",
        correlationId: "corr-stale",
        retryable: false,
      };
    });
    render(
      <DecisionPanel
        decision={{ id: "decision-3", status: "pending", version: 4 }}
        onSubmit={onSubmit}
        onConflict={onConflict}
      />,
    );

    const reason = screen.getByLabelText("裁决理由");
    await userEvent.type(reason, "保留本地理由");
    await userEvent.click(screen.getByRole("button", { name: "提交裁决" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("裁决已变化，请核对最新版本后重试");
    expect(reason).toHaveValue("保留本地理由");
    expect(reason).not.toBeDisabled();
    expect(onConflict).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "裁决" })).toHaveFocus();
  });

  it("locks a pending record whose authoritative expiry has passed", async () => {
    render(
      <DecisionPanel
        decision={{
          id: "decision-expired",
          status: "pending",
          version: 2,
          expiresAt: "2020-01-01T00:00:00Z",
        }}
        onSubmit={vi.fn()}
      />,
    );

    expect(await screen.findByText("已过期")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交裁决" })).toBeDisabled();
  });

  it("lets a same-id authoritative resolution override a prior local expiry", async () => {
    const { rerender } = render(
      <DecisionPanel
        decision={{
          id: "decision-expired-then-resolved",
          status: "pending",
          version: 2,
          expiresAt: "2020-01-01T00:00:00Z",
        }}
        onSubmit={vi.fn()}
      />,
    );
    expect(await screen.findByText("已过期")).toBeInTheDocument();

    rerender(
      <DecisionPanel
        decision={{
          id: "decision-expired-then-resolved",
          status: "resolved",
          version: 3,
          expiresAt: "2020-01-01T00:00:00Z",
          resolvedAction: "approve",
          resolvedReason: "服务端已裁决",
        }}
        onSubmit={vi.fn()}
      />,
    );

    expect(await screen.findByText("已裁决")).toBeInTheDocument();
    expect(screen.queryByText("已过期")).not.toBeInTheDocument();
    expect(screen.getByLabelText("裁决理由")).toHaveValue("服务端已裁决");
    expect(screen.getByLabelText("裁决理由")).toBeDisabled();
  });
});
