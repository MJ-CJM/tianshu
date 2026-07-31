// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import PageQueryError from "./PageQueryError";

afterEach(cleanup);

describe("PageQueryError", () => {
  it("preserves a service failure instead of rendering successful empty data", async () => {
    const retry = vi.fn();
    render(
      <PageQueryError
        error={{
          status: 503,
          code: "service-unavailable",
          message: "ledger offline",
          correlationId: "corr-ledger",
          retryable: true,
        }}
        onRetry={retry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("ledger offline");
    expect(screen.queryByText("暂无数据")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
