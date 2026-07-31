// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../i18n", () => ({
  useT: () => (key: string) =>
    ({
      "maturity.experimental": "实验",
      "maturity.canDo": "能做什么",
      "maturity.currentBoundary": "当前边界",
    })[key] ?? key,
}));

import PageContainer from "../common/PageContainer";
import { CapabilityBoundary, MaturityBadge } from "./CapabilityMaturity";

describe("capability maturity presentation", () => {
  it("keeps the maturity text beside, but outside, the page heading", () => {
    render(
      <PageContainer
        title="自进化"
        titleBadge={<MaturityBadge maturity="experimental" />}
      >
        <div>内容</div>
      </PageContainer>,
    );

    expect(screen.getByRole("heading", { name: "自进化" })).toBeInTheDocument();
    expect(screen.getByText("实验")).toBeInTheDocument();
  });

  it("states both the supported behavior and the current boundary in text", () => {
    render(
      <CapabilityBoundary
        maturity="experimental"
        canDo="查看候选和门禁"
        boundary="不自动晋升"
      />,
    );

    expect(screen.getByText("能做什么：")).toBeInTheDocument();
    expect(screen.getByText("查看候选和门禁")).toBeInTheDocument();
    expect(screen.getByText("当前边界：")).toBeInTheDocument();
    expect(screen.getByText("不自动晋升")).toBeInTheDocument();
  });
});
