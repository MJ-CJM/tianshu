// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EdictDetailSnapshotV1 } from "../api/edicts";
import type { ApiProblem, PageDataStatus } from "../contracts/api";
import { useLocaleProvider } from "../hooks/useLocale";

const detailHook = vi.hoisted(() => ({ useEdictDetail: vi.fn() }));
const legacyApprovals = vi.hoisted(() => ({
  pendingTools: [] as Array<Record<string, unknown>>,
}));

vi.mock("../hooks/useEdictDetail", () => ({ useEdictDetail: detailHook.useEdictDetail }));
vi.mock("../hooks/useDag", () => ({ useDagByEdict: () => ({ data: null }) }));
vi.mock("../hooks/useApprovals", () => ({
  usePendingToolCalls: () => ({ data: legacyApprovals.pendingTools }),
}));
vi.mock("../hooks/usePersonas", () => ({ usePersonas: () => ({ data: [] }) }));
vi.mock("../components/policy/PolicyTimeline", () => ({ PolicyTimeline: () => null }));
vi.mock("../components/edict/OuterLoopTimeline", () => ({ default: () => null }));
vi.mock("../components/edict/SupervisionReportCard", () => ({ default: () => null }));
vi.mock("../components/edict/FollowUpOverridePanel", () => ({ default: () => null }));

import EdictDetailPage from "./EdictDetailPage";

const REQUESTED_CONTRACT = {
  schema_version: "1",
  executor: { adapter_id: "requested-native" },
  capabilities: {
    mandatory: ["action_interception", "workspace_control"],
    advisory: ["network_control", "pause", "budget_enforcement"],
  },
};

const SNAPSHOT = {
  schema_version: 1,
  edict: {
    id: "edict-1",
    title: "核验发布候选",
    goal: "用真实治理记录核验发布候选",
    context: "只读核验",
    status: "open",
    created_at: "2026-07-17T08:00:00Z",
    priority: "normal",
    review_policy: "always",
    schedule: { type: "immediate", at: null, cron: null, timezone: "Asia/Shanghai" },
    runtime: {
      timeout_seconds: 300,
      max_iterations: 20,
      max_concurrency: 1,
      retry_limit: 0,
      token_budget: null,
      cost_budget_cny: null,
      approval_required_tools: [],
      lifecycle_phase: "active",
    },
    constraints: [],
    output_format: null,
    source: "web",
    submitter: "principal-owner",
    acceptance: null,
    governance_contract: REQUESTED_CONTRACT,
  },
  memorials: [],
  runs: [
    {
      memorial_id: "memorial-1",
      phase: "waiting_decision",
      version: 7,
      checkpoint_present: true,
      side_effect_cursor: 3,
      pending_decision_id: "decision-pending",
      resolved_decision_id: "decision-resolved",
      plan_lineage: [
        {
          revision_id: "lineage-2",
          parent_revision_id: "lineage-1",
          plan_hash: "b".repeat(64),
          reason_code: "review_update",
          reason_summary: "人工复核后更新",
          artifact_digest: "c".repeat(64),
          created_at: "2026-07-17T08:10:00Z",
        },
      ],
      effective_contract: {
        requested_contract_hash: "d".repeat(64),
        executor: { adapter_id: "effective-contained" },
        executor_manifest_id: "contained.v1",
        executor_manifest_version: "1",
        runtime_probe_id: "probe-1",
        effective_controls: [
          { capability: "action_interception", requested_mode: "mandatory", state: "enforced", evidence: [] },
          { capability: "workspace_control", requested_mode: "mandatory", state: "enforced", evidence: [] },
          { capability: "network_control", requested_mode: "advisory", state: "best_effort", evidence: [] },
          { capability: "pause", requested_mode: "advisory", state: "unsupported", evidence: [] },
          { capability: "budget_enforcement", requested_mode: "advisory", state: "unsupported", evidence: [] },
        ],
        unsupported_advisory: ["pause", "budget_enforcement"],
      },
      updated_at: "2026-07-17T08:20:00Z",
    },
  ],
  decisions: [
    {
      request: {
        decision_request_id: "decision-pending",
        kind: "governed_apply",
        edict_id: "edict-1",
        memorial_id: "memorial-1",
        payload: { permission_boundary: "workspace", restore_point: "restore-1" },
        requested_by: "orchestrator",
        expires_at: "2099-07-17T09:00:00Z",
        status: "pending",
        version: 4,
        created_at: "2026-07-17T08:15:00Z",
        updated_at: "2026-07-17T08:15:00Z",
      },
      resolution: null,
    },
    {
      request: {
        decision_request_id: "decision-resolved",
        kind: "plan_review",
        edict_id: "edict-1",
        memorial_id: "memorial-1",
        payload: {},
        requested_by: "orchestrator",
        expires_at: "2026-07-17T08:30:00Z",
        status: "resolved",
        version: 2,
        created_at: "2026-07-17T08:05:00Z",
        updated_at: "2026-07-17T08:06:00Z",
      },
      resolution: {
        action: "approve",
        reason: "方案已核验",
        actor_principal_id: "principal-reviewer",
        actor_display_name: "复核官",
        resolved_at: "2026-07-17T08:06:00Z",
      },
    },
  ],
  evidence: [
    {
      bundle_id: "bundle-1",
      memorial_id: "memorial-1",
      status: "closed",
      version: 5,
      content_hash: "a".repeat(64),
      created_at: "2026-07-17T08:20:00Z",
      closed_at: "2026-07-17T08:25:00Z",
      download_available: true,
      executor: {
        adapter_id: "executor-content",
        display_name: "Contained Executor",
        level: "contained",
        manifest_hash: "e".repeat(64),
      },
      artifacts: [{ digest: "f".repeat(64), size_bytes: 128, media_type: "application/json", redaction: "none" }],
      checks: [{
        check_id: "check-1",
        name: "release-tests",
        status: "passed",
        command_fingerprint: "g".repeat(64),
        exit_code: 0,
        output_artifact_digest: "f".repeat(64),
        started_at: "2026-07-17T08:21:00Z",
        completed_at: "2026-07-17T08:22:00Z",
      }],
      decisions: [],
      effects: [],
      cost: { currency: "CNY", requested_budget: "12", effective_budget: "10", actual_cost: "1.25", prompt_tokens: 100, completion_tokens: 50, cache_read_tokens: 10 },
      environment: { tianshu_version: "0.4.2", python_version: "3.13", platform: "darwin", architecture: "arm64", dependency_lock_hash: "1".repeat(64), environment_fingerprint: "2".repeat(64) },
      auditor: { auditor_id: "auditor-independent", verdict: "pass", reason: "证据完整", required_evidence: [], missing_evidence: [], evaluated_at: "2026-07-17T08:24:00Z" },
      requirements: { check_names: ["release-tests"], decision_request_ids: [], effect_intent_ids: [], artifact_digests: ["f".repeat(64)] },
    },
  ],
} as unknown as EdictDetailSnapshotV1;

function problem(status: number, code: string, message: string): ApiProblem {
  return { status, code, message, correlationId: `corr-${status}`, retryable: status >= 500 };
}

function hookState(
  status: PageDataStatus,
  detail: EdictDetailSnapshotV1 | null,
  apiProblem: ApiProblem | null = null,
) {
  return {
    detail,
    edict: detail?.edict ?? null,
    memorials: detail?.memorials ?? [],
    events: [],
    status,
    problem: apiProblem,
    isLoading: status === "loading",
    error: apiProblem,
    refetch: vi.fn(),
    resolveDecision: vi.fn().mockResolvedValue({ status: "resolved", version: 5 }),
    replay: vi.fn().mockResolvedValue("edict-replay"),
  };
}

function LocationProbe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

function LocaleControls() {
  const locale = useLocaleProvider();
  return (
    <>
      <button type="button" onClick={() => locale.setLocale("zh-modern")}>modern-locale</button>
      <button type="button" onClick={() => locale.setLocale("en")}>english-locale</button>
      <button type="button" onClick={() => locale.setLocale("zh-classic")}>classic-locale</button>
    </>
  );
}

function renderPage(showLocaleControls = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/edicts/edict-1"]}>
        <LocationProbe />
        {showLocaleControls ? <LocaleControls /> : null}
        <Routes>
          <Route path="/edicts/:edictId" element={<EdictDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
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
  detailHook.useEdictDetail.mockReset();
  legacyApprovals.pendingTools = [];
});

afterEach(() => cleanup());

describe("Edict detail durable governance workspace", () => {
  it("returns to the Royal Study and localizes the memorial audit verdict", async () => {
    const detail = {
      ...SNAPSHOT,
      memorials: [
        {
          id: "memorial-audited",
          edict_id: "edict-1",
          instruction: "核验返回路径与审计文案",
          status: "completed",
          summary: null,
          result: "已完成",
          usage: {
            prompt_tokens: 0,
            completion_tokens: 0,
            total_tokens: 0,
          },
          error: null,
          created_at: "2026-07-17T08:00:00Z",
          started_at: "2026-07-17T08:00:00Z",
          completed_at: "2026-07-17T08:01:00Z",
          attempt: 1,
          parent_memorial_id: null,
          review_status: "not_required",
          audit: {
            verdict: "pass",
            reasons: [],
            rules_checked: 1,
            llm_reviewed: false,
          },
          artifacts: [],
          timeline: [],
          persona_id: null,
          dag_node_id: null,
        },
      ],
    } as unknown as EdictDetailSnapshotV1;
    detailHook.useEdictDetail.mockReturnValue(hookState("success-data", detail));
    const user = userEvent.setup();

    renderPage();

    expect(screen.getAllByText("通过").length).toBeGreaterThan(0);
    expect(screen.queryByText("audit.label.pass")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /返回御书房/ }));
    expect(screen.getByLabelText("location")).toHaveTextContent("/approvals");
  });

  it("keeps legacy event, tool, and memorial review records read-only and mutates only the composed decision", async () => {
    const legacyDetail = {
      ...SNAPSHOT,
      memorials: [
        {
          id: "memorial-1",
          edict_id: "edict-1",
          instruction: "旧审核记录",
          status: "completed",
          summary: null,
          result: null,
          usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
          error: null,
          created_at: "2026-07-17T08:00:00Z",
          started_at: "2026-07-17T08:00:00Z",
          completed_at: "2026-07-17T08:10:00Z",
          attempt: 1,
          parent_memorial_id: null,
          review_status: "pending",
          audit: null,
          artifacts: [],
          timeline: [],
          persona_id: null,
          dag_node_id: null,
        },
      ],
    } as unknown as EdictDetailSnapshotV1;
    const state = {
      ...hookState("success-data", legacyDetail),
      events: [
        {
          id: "event-plan-pending",
          event_type: "plan.pending_review",
          created_at: "2026-07-17T08:11:00Z",
          edict_id: "edict-1",
          memorial_id: "memorial-1",
          payload: {
            plan: {
              tasks: [
                {
                  task_id: "legacy-task",
                  description: "旧方案任务",
                  assigned_official: "executor",
                  depends_on: [],
                  tools_required: [],
                },
              ],
            },
          },
        },
      ],
    };
    legacyApprovals.pendingTools = [
      {
        decision_request_id: "legacy-tool-decision",
        memorial_id: "memorial-1",
        edict_id: "edict-1",
        tool_name: "legacy.tool",
        rule_id: null,
        reason: "旧工具审批",
        tool_tier: "write",
        args_summary: {},
        created_at: "2026-07-17T08:12:00Z",
      },
    ];
    detailHook.useEdictDetail.mockReturnValue(state);
    const user = userEvent.setup();

    renderPage();

    expect(screen.getByText("旧方案任务")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准（执行此方案）" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "驳（驳回方案）" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准/驳" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "待裁决" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重办" })).not.toBeInTheDocument();
    expect(screen.queryByText("legacy.tool")).not.toBeInTheDocument();

    // 审计区默认折叠,展开后才能操作裁决表单
    await user.click(screen.getByText(/治理稽核|治理与审计|Governance & audit/));

    await user.type(screen.getAllByLabelText("裁决理由")[0]!, "只走权威裁决");
    await user.click(screen.getAllByRole("button", { name: "提交裁决" })[0]!);

    await waitFor(() => expect(state.resolveDecision).toHaveBeenCalledWith({
      decisionRequestId: "decision-pending",
      kind: "governed_apply",
      action: "approve",
      reason: "只走权威裁决",
      expectedVersion: 4,
    }));
  });

  it("renders one authoritative snapshot with requested/effective contract, real run lineage, durable decisions, and closed evidence", async () => {
    const state = hookState("success-data", SNAPSHOT);
    detailHook.useEdictDetail.mockReturnValue(state);
    const user = userEvent.setup();
    renderPage();

    // 审计区默认折叠,展开后审计内容才可见(getByRole 忽略隐藏元素)
    await user.click(screen.getByText(/治理稽核|治理与审计|Governance & audit/));

    const governance = screen.getByRole("heading", { name: "治理契约" }).closest("section");
    expect(governance).not.toBeNull();
    expect(within(governance!).getByText("requested-native")).toBeInTheDocument();
    expect(within(governance!).getByText("effective-contained")).toBeInTheDocument();
    expect(within(governance!).queryByText("pause")).not.toBeInTheDocument();
    expect(within(governance!).queryByText("budget_enforcement")).not.toBeInTheDocument();
    expect(screen.getByText("waiting_decision")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("lineage-2")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "裁决" })).toHaveLength(2);
    expect(screen.getByText(/principal-reviewer/)).toBeInTheDocument();
    expect(screen.getByText("executor-content")).toBeInTheDocument();
    expect(screen.getByText("auditor-independent")).toBeInTheDocument();
    expect(screen.getByText("release-tests: 通过 · exit 0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载证据包" })).toHaveAttribute(
      "href",
      "/api/evidence/bundle-1/download",
    );

    await user.click(screen.getByRole("button", { name: "受治理重放" }));
    await waitFor(() => expect(state.replay).toHaveBeenCalledWith({
      title: "核验发布候选",
      goal: "用真实治理记录核验发布候选",
      context: "只读核验",
      priority: "normal",
      governanceContract: REQUESTED_CONTRACT,
    }));
    await waitFor(() => {
      expect(screen.getByLabelText("location")).toHaveTextContent("/edicts/edict-replay");
    });
  });

  it("shows precise successful empty copy without invented counts", () => {
    const empty = { ...SNAPSHOT, runs: [], decisions: [], evidence: [] };
    detailHook.useEdictDetail.mockReturnValue(hookState("success-empty", empty));
    renderPage();

    expect(screen.getByText("尚无治理运行实录。")).toBeInTheDocument();
    expect(screen.getByText("尚无持久裁决实录。")).toBeInTheDocument();
    expect(screen.getByText("尚无可验凭据包。")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/可信度|置信度|88%/);
  });

  it("shows pause only while a long-task memorial is actually active", () => {
    const completed = {
      ...SNAPSHOT,
      edict: {
        ...SNAPSHOT.edict,
        acceptance: { max_outer_iterations: 5 },
      },
      memorials: [
        {
          id: "memorial-completed",
          edict_id: "edict-1",
          instruction: "已完成的长任务",
          status: "completed",
          review_status: "not_required",
          usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
          created_at: "2026-07-17T08:00:00Z",
          started_at: "2026-07-17T08:00:00Z",
          completed_at: "2026-07-17T08:01:00Z",
          attempt: 1,
          artifacts: [],
          timeline: [],
        },
      ],
    } as unknown as EdictDetailSnapshotV1;
    detailHook.useEdictDetail.mockReturnValue(hookState("success-data", completed));
    const rendered = renderPage();

    expect(screen.queryByRole("button", { name: /本轮.*暂停/ })).not.toBeInTheDocument();

    rendered.unmount();
    const running = {
      ...completed,
      memorials: [
        {
          ...completed.memorials[0],
          id: "memorial-running",
          status: "running",
          completed_at: null,
        },
      ],
    } as unknown as EdictDetailSnapshotV1;
    detailHook.useEdictDetail.mockReturnValue(hookState("success-data", running));
    renderPage();

    expect(screen.getByRole("button", { name: /本轮.*暂停/ })).toBeInTheDocument();
  });

  it("makes planner fallback visible with localized reasons", async () => {
    const state = {
      ...hookState("success-data", SNAPSHOT),
      events: [
        {
          id: "event-plan-fallback",
          event_type: "plan.completed",
          created_at: "2026-07-17T08:11:00Z",
          edict_id: "edict-1",
          memorial_id: "memorial-1",
          payload: {
            plan: {
              planning_mode: "fallback",
              fallback_reason: "llm_error",
              tasks: [
                {
                  task_id: "fallback-task",
                  description: "按单任务继续",
                  assigned_official: "executor",
                  depends_on: [],
                  tools_required: [],
                },
              ],
            },
          },
        },
      ],
    };
    detailHook.useEdictDetail.mockReturnValue(state);
    const user = userEvent.setup();
    renderPage(true);

    await user.click(screen.getByRole("button", { name: "modern-locale" }));
    expect(screen.getByText("智能规划未生效，当前按单任务执行")).toBeInTheDocument();
    expect(screen.getByText("规划模型调用失败。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "english-locale" }));
    expect(
      screen.getByText("Smart planning was unavailable; this is running as one task"),
    ).toBeInTheDocument();
    expect(screen.getByText("The planning model request failed.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "classic-locale" }));
    expect(screen.getByText("智能筹划未成，现按单项差事施行")).toBeInTheDocument();
    expect(screen.getByText("筹划模型调用未成。")).toBeInTheDocument();
  });

  it("provides precise durable empty states in all three locales", async () => {
    const empty = { ...SNAPSHOT, runs: [], decisions: [], evidence: [] };
    detailHook.useEdictDetail.mockReturnValue(hookState("success-empty", empty));
    const user = userEvent.setup();
    renderPage(true);

    expect(screen.getByText("尚无治理运行实录。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "modern-locale" }));
    expect(screen.getByText("当前还没有治理运行记录。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "english-locale" }));
    expect(screen.getByText("No governed run has been recorded yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "classic-locale" }));
  });

  it("keeps the last authoritative snapshot visible when refresh is stale", () => {
    detailHook.useEdictDetail.mockReturnValue(
      hookState("stale", SNAPSHOT, problem(500, "detail-refresh-failed", "刷新失败")),
    );
    renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent("刷新失败");
    expect(screen.getByText("waiting_decision")).toBeInTheDocument();
  });

  it("shows loading without fabricated authority", () => {
    detailHook.useEdictDetail.mockReturnValue(hookState("loading", null));
    renderPage();

    expect(screen.getByRole("heading", { name: "正在加载" })).toBeInTheDocument();
    expect(screen.queryByText("治理契约")).not.toBeInTheDocument();
  });

  it.each([
    [401, "auth-required", "登录已失效"],
    [403, "permission-denied", "无权读取敕令详情"],
  ])("shows %i as an explicit permission state", (statusCode, code, message) => {
    detailHook.useEdictDetail.mockReturnValue(
      hookState("permission-denied", null, problem(statusCode, code, message)),
    );
    renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent("无权查看此内容");
    expect(screen.getByRole("alert")).toHaveTextContent(message);
  });

  it("shows an explicit 503 unavailable state", () => {
    detailHook.useEdictDetail.mockReturnValue(
      hookState("service-unavailable", null, problem(503, "service-unavailable", "详情服务暂不可用")),
    );
    renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("详情服务暂不可用");
  });

  it("shows an explicit generic error without rendering stale authority", () => {
    detailHook.useEdictDetail.mockReturnValue(
      hookState("error", null, problem(500, "detail-failed", "详情读取失败")),
    );
    renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent("请求失败");
    expect(screen.getByRole("alert")).toHaveTextContent("详情读取失败");
    expect(screen.queryByText("治理契约")).not.toBeInTheDocument();
  });
});
