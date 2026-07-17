// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiProblem } from "../contracts/api";
import type { ControlCenterSnapshotV1 } from "../api/control";
import { useLocaleProvider } from "../hooks/useLocale";
import { CONTROL_CENTER_QUERY_KEY } from "../hooks/useControlCenter";

const controlApi = vi.hoisted(() => ({ getControlCenterSnapshot: vi.fn() }));

vi.mock("../api/control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/control")>()),
  getControlCenterSnapshot: controlApi.getControlCenterSnapshot,
}));

import ControlCenterPage from "./ControlCenterPage";

const SNAPSHOT: ControlCenterSnapshotV1 = {
  schema_version: 1,
  generated_at: "2026-07-17T09:00:00Z",
  readiness: "ready",
  active_run_total: 25,
  pending_decision_total: 24,
  evidence_total: 23,
  active_runs: [
    {
      edict_id: "edict-1",
      edict_title: "验证发布候选",
      memorial_id: "memorial-1",
      phase: "executing",
      updated_at: "2026-07-17T08:59:00Z",
    },
  ],
  pending_decisions: [
    {
      decision_request_id: "decision-1",
      edict_id: "edict-1",
      edict_title: "验证发布候选",
      memorial_id: "memorial-1",
      kind: "tool",
      expires_at: "2026-07-17T09:10:00Z",
      created_at: "2026-07-17T08:58:00Z",
    },
  ],
  recent_evidence: [
    {
      bundle_id: "bundle-1",
      edict_id: "edict-1",
      edict_title: "验证发布候选",
      memorial_id: "memorial-1",
      status: "closed",
      content_hash: "a".repeat(64),
      created_at: "2026-07-17T08:50:00Z",
      closed_at: "2026-07-17T08:55:00Z",
    },
  ],
  evolution_status: "not_enabled",
};

const EMPTY_SNAPSHOT: ControlCenterSnapshotV1 = {
  ...SNAPSHOT,
  active_run_total: 0,
  pending_decision_total: 0,
  evidence_total: 0,
  active_runs: [],
  pending_decisions: [],
  recent_evidence: [],
};

function problem(status: number, code: string, message: string): ApiProblem {
  return {
    status,
    code,
    message,
    correlationId: "corr-control",
    retryable: status >= 500,
  };
}

function LocaleControls() {
  const locale = useLocaleProvider();
  return (
    <>
      <button type="button" onClick={() => locale.setLocale("en")}>switch-English</button>
      <button type="button" onClick={() => locale.setLocale("zh-classic")}>switch-classic</button>
    </>
  );
}

function renderPage(queryClient?: QueryClient, showLocaleControls = false) {
  const client =
    queryClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/control"]}>
          {showLocaleControls ? <LocaleControls /> : null}
          <ControlCenterPage />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  controlApi.getControlCenterSnapshot.mockReset();
});

afterEach(() => cleanup());

describe("real Control Center snapshot", () => {
  it("shows loading while the single aggregate request is pending", () => {
    controlApi.getControlCenterSnapshot.mockReturnValue(new Promise(() => undefined));
    renderPage();

    expect(screen.getByRole("heading", { name: "中枢总览" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "正在加载" })).toBeInTheDocument();
    expect(controlApi.getControlCenterSnapshot).toHaveBeenCalledTimes(1);
  });

  it("renders precise empty copy without invented activity", async () => {
    controlApi.getControlCenterSnapshot.mockResolvedValue(EMPTY_SNAPSHOT);
    renderPage();

    expect(await screen.findByText("当前没有进行中的治理运行。")).toBeInTheDocument();
    expect(screen.getByText("当前没有待裁决事项。")).toBeInTheDocument();
    expect(screen.getByText("当前还没有可核验的证据束。")).toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(3);
  });

  it("renders real counts and keyboard-accessible Edict Decision and Evidence links", async () => {
    controlApi.getControlCenterSnapshot.mockResolvedValue(SNAPSHOT);
    const user = userEvent.setup();
    renderPage();

    const edictLink = await screen.findByRole("link", { name: "查看敕令" });
    const decisionLink = screen.getByRole("link", { name: "查看并裁决" });
    const evidenceLink = screen.getByRole("link", { name: "下载证据" });
    expect(edictLink).toHaveAttribute("href", "/edicts/edict-1");
    expect(decisionLink).toHaveAttribute("href", "/approvals");
    expect(evidenceLink).toHaveAttribute("href", "/api/evidence/bundle-1/download");
    await user.tab();
    expect(document.activeElement).toBe(edictLink);
    expect(screen.getByText("25")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
    expect(screen.getByText("23")).toBeInTheDocument();
    expect(screen.getByText("尚未启用")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/系统可信|置信度|信心分|88%/);
    expect(controlApi.getControlCenterSnapshot).toHaveBeenCalledTimes(1);
  });

  it("keeps the previous snapshot visible when refresh becomes stale", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(CONTROL_CENTER_QUERY_KEY, SNAPSHOT);
    controlApi.getControlCenterSnapshot.mockRejectedValue(
      problem(500, "control-refresh-failed", "刷新中枢快照失败"),
    );
    renderPage(client);

    expect(await screen.findByRole("heading", { name: "数据可能已过期" })).toBeInTheDocument();
    expect(screen.getAllByText("验证发布候选")).toHaveLength(3);
    expect(screen.getByText("刷新中枢快照失败")).toBeInTheDocument();
  });

  it("renders a generic error without substituting zeros", async () => {
    controlApi.getControlCenterSnapshot.mockRejectedValue(
      problem(500, "control-failed", "中枢快照读取失败"),
    );
    renderPage();

    expect(await screen.findByRole("heading", { name: "请求失败" })).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("renders permission denied distinctly", async () => {
    controlApi.getControlCenterSnapshot.mockRejectedValue(
      problem(403, "permission-denied", "当前身份无权查看中枢"),
    );
    renderPage();

    expect(await screen.findByRole("heading", { name: "无权查看此内容" })).toBeInTheDocument();
    expect(screen.getByText("关联标识: corr-control")).toBeInTheDocument();
  });

  it("renders service unavailable distinctly and offers retry", async () => {
    controlApi.getControlCenterSnapshot.mockRejectedValue(
      problem(503, "control_center_unavailable", "中枢数据源暂不可用"),
    );
    renderPage();

    expect(await screen.findByRole("heading", { name: "服务暂不可用" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    await waitFor(() => expect(controlApi.getControlCenterSnapshot).toHaveBeenCalledTimes(1));
  });

  it("does not offer the closed-bundle download for open evidence", async () => {
    controlApi.getControlCenterSnapshot.mockResolvedValue({
      ...SNAPSHOT,
      recent_evidence: [
        {
          ...SNAPSHOT.recent_evidence[0]!,
          status: "open",
          content_hash: null,
          closed_at: null,
        },
      ],
    });
    renderPage();

    expect(await screen.findByText("生成中")).toBeInTheDocument();
    expect(screen.getByText("等待封存")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载证据" })).not.toBeInTheDocument();
  });

  it("keeps evolution not enabled when overall readiness is degraded", async () => {
    controlApi.getControlCenterSnapshot.mockResolvedValue({
      ...EMPTY_SNAPSHOT,
      readiness: "degraded",
      evolution_status: "not_enabled",
    });
    renderPage();

    expect(await screen.findByText("服务降级")).toBeInTheDocument();
    expect(screen.getByText("尚未启用")).toBeInTheDocument();
    expect(screen.queryByText("随系统降级")).not.toBeInTheDocument();
  });

  it("uses the English locale for Control Center copy", async () => {
    controlApi.getControlCenterSnapshot.mockResolvedValue(EMPTY_SNAPSHOT);
    const user = userEvent.setup();
    renderPage(undefined, true);
    await user.click(screen.getByRole("button", { name: "switch-English" }));

    expect(screen.getByRole("heading", { name: "Control Center" })).toBeInTheDocument();
    expect(await screen.findByText("There are no active governance runs.")).toBeInTheDocument();
    expect(screen.getByText("There are no pending decisions.")).toBeInTheDocument();
    expect(screen.getByText("There are no verifiable evidence bundles yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "switch-classic" }));
  });
});
