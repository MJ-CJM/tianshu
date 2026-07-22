// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiProblem, PageDataStatus } from "../../contracts/api";
import PageDataState from "./PageDataState";

const retryableProblem: ApiProblem = {
  status: 503,
  code: "service-unavailable",
  message: "后端暂不可用",
  correlationId: "corr-503",
  retryable: true,
};

function renderState(
  status: PageDataStatus,
  options: { data?: string[] | null; problem?: ApiProblem; onRetry?: () => void } = {},
) {
  return render(
    <PageDataState
      status={status}
      data={options.data ?? null}
      problem={options.problem}
      isEmpty={(items) => items.length === 0}
      onRetry={options.onRetry}
    >
      {(items) => <div>records:{items.join(",")}</div>}
    </PageDataState>,
  );
}

afterEach(cleanup);

describe("PageDataState seven-state contract", () => {
  it("renders loading as a polite status", () => {
    renderState("loading");

    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
  });

  it("renders success-empty without inventing data", () => {
    renderState("success-empty", { data: [] });

    expect(screen.getByRole("status")).toHaveTextContent("暂无数据");
    expect(screen.queryByText(/records:/)).not.toBeInTheDocument();
  });

  it("renders success-data through the child function", () => {
    renderState("success-data", { data: ["one"] });

    expect(screen.getByText("records:one")).toBeInTheDocument();
  });

  it("keeps stale data visible with correlation and a safe retry", async () => {
    const onRetry = vi.fn();
    renderState("stale", { data: ["old"], problem: retryableProblem, onRetry });

    expect(screen.getByText("records:old")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("数据可能已过期");
    expect(screen.getByRole("alert")).toHaveTextContent("corr-503");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders an ordinary error without retry when it is unsafe", () => {
    renderState("error", {
      problem: { ...retryableProblem, status: 500, retryable: false, correlationId: "corr-500" },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("请求失败");
    expect(screen.getByRole("alert")).toHaveTextContent("corr-500");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("renders permission-denied distinctly", () => {
    renderState("permission-denied", {
      problem: { ...retryableProblem, status: 403, code: "permission-denied", retryable: false },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("无权查看此内容");
  });

  it("renders service-unavailable distinctly with retry", () => {
    renderState("service-unavailable", { problem: retryableProblem, onRetry: vi.fn() });

    expect(screen.getByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
