// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import GovernanceContractCard from "./GovernanceContractCard";

afterEach(cleanup);

describe("GovernanceContractCard", () => {
  it("shows managed capability truth, mandatory mismatch and advisory gaps", () => {
    render(
      <GovernanceContractCard
        executorLevel="managed"
        requestedExecutor="codex"
        effectiveExecutor="codex-managed"
        capabilities={[
          { id: "workspace_control", label: "工作区控制", requested: "mandatory", effective: "enforced" },
          { id: "network_control", label: "网络边界", requested: "mandatory", effective: "unsupported" },
        ]}
        mandatoryMismatches={["网络边界不可强制"]}
        advisoryGaps={["缺少预运行恢复点"]}
      />,
    );

    expect(screen.getByRole("heading", { name: "治理契约" })).toBeInTheDocument();
    expect(screen.getByText("网络边界不可强制")).toBeInTheDocument();
    expect(screen.getByText("缺少预运行恢复点")).toBeInTheDocument();
    expect(screen.getByText("不支持")).toBeInTheDocument();
  });

  it("does not claim managed-only controls for a contained executor", () => {
    render(
      <GovernanceContractCard
        executorLevel="contained"
        requestedExecutor="claude-cli"
        effectiveExecutor="claude-cli-contained"
        capabilities={[
          { id: "action_interception", label: "启动边界", requested: "mandatory", effective: "enforced" },
          { id: "decision_bridge", label: "逐工具裁决", requested: "mandatory", effective: "enforced" },
          { id: "budget_enforcement", label: "硬成本上限", requested: "mandatory", effective: "enforced" },
          { id: "workspace_control", label: "工作区控制", requested: "mandatory", effective: "enforced" },
          { id: "network_control", label: "网络边界", requested: "mandatory", effective: "best_effort" },
          { id: "governed_apply_merge", label: "受治理应用", requested: "advisory", effective: "observed" },
          { id: "pause", label: "暂停控制", requested: "advisory", effective: "unsupported" },
        ]}
        mandatoryMismatches={[]}
        advisoryGaps={[]}
      />,
    );

    expect(screen.queryByText("逐工具裁决")).not.toBeInTheDocument();
    expect(screen.queryByText("硬成本上限")).not.toBeInTheDocument();
    expect(screen.queryByText("暂停控制")).not.toBeInTheDocument();
    expect(screen.getByText("启动边界")).toBeInTheDocument();
    expect(screen.getByText("网络边界")).toBeInTheDocument();
    expect(screen.getByText("工作区控制")).toBeInTheDocument();
    expect(screen.getByText("受治理应用")).toBeInTheDocument();
    expect(screen.getByText("仅展示可验证的容器边界，不承诺托管级控制。")).toBeInTheDocument();
  });
});
