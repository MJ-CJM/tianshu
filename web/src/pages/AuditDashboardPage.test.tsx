// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiProblem } from "../contracts/api";
import type { AuditStats } from "../api/types";
import AuditDashboardPage from "./AuditDashboardPage";

const apiMocks = vi.hoisted(() => ({
  useAuditStats: vi.fn(),
  useAuditRules: vi.fn(),
  fetchPolicyStats: vi.fn(),
  getFailureDistribution: vi.fn(),
  listNetworkEvents: vi.fn(),
  apiGet: vi.fn(),
}));

vi.mock("../hooks/useAudit", () => ({
  useAuditStats: apiMocks.useAuditStats,
}));
vi.mock("../hooks/useOps", () => ({
  useAuditRules: apiMocks.useAuditRules,
}));
vi.mock("../api/policy", () => ({
  fetchPolicyStats: apiMocks.fetchPolicyStats,
}));
vi.mock("../api/evals", () => ({
  getFailureDistribution: apiMocks.getFailureDistribution,
}));
vi.mock("../api/network_events", () => ({
  listNetworkEvents: apiMocks.listNetworkEvents,
}));
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, default: { get: apiMocks.apiGet } };
});
vi.mock("../components/ops/EventBusTab", () => ({ EventBusTab: () => null }));
vi.mock("../components/ops/WorkersTab", () => ({ WorkersTab: () => null }));
vi.mock("../components/ops/HooksTab", () => ({ HooksTab: () => null }));

const emptyStats: AuditStats = {
  summary: {
    total_memorials: 0,
    total_prompt_tokens: 0,
    total_completion_tokens: 0,
    total_cache_read_tokens: 0,
    total_tokens: 0,
    audit_pass: 0,
    audit_flag: 0,
    audit_block: 0,
    review_pending: 0,
    review_approved: 0,
    review_rejected: 0,
  },
  per_edict: [],
  recent_audits: [],
};
const getComputedStyle = window.getComputedStyle.bind(window);

function problem(status: number, code: string, message: string): ApiProblem {
  return {
    status,
    code,
    message,
    correlationId: `corr-${status}`,
    retryable: status >= 500,
  };
}

function queryState<T>({
  data,
  isLoading = false,
  error = null,
}: {
  data?: T;
  isLoading?: boolean;
  error?: unknown;
}) {
  return { data, isLoading, error, refetch: vi.fn() };
}

function renderPage(path = "/audit") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AuditDashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.defineProperty(window, "getComputedStyle", {
    configurable: true,
    value: (element: Element) => getComputedStyle(element),
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
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
  apiMocks.useAuditStats.mockReturnValue(queryState({ data: emptyStats }));
  apiMocks.useAuditRules.mockReturnValue(
    queryState({ data: { rules: [], review_policies: [] } }),
  );
  apiMocks.fetchPolicyStats.mockResolvedValue({
    allow: 0,
    deny: 0,
    require_approval: 0,
    approved: 0,
    rejected: 0,
  });
  apiMocks.getFailureDistribution.mockResolvedValue({
    success: true,
    data: [],
    error: null,
    metadata: null,
  });
  apiMocks.listNetworkEvents.mockResolvedValue([]);
  apiMocks.apiGet.mockResolvedValue({ data: { data: [] } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AuditDashboardPage truthful data states", () => {
  it("shows stats loading without false zero summaries", () => {
    apiMocks.useAuditStats.mockReturnValue(queryState({ isLoading: true }));

    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    expect(screen.queryByText("Token 总量")).not.toBeInTheDocument();
    expect(screen.queryByText("奏折总数")).not.toBeInTheDocument();
  });

  it("keeps successful zero stats and empty failure attribution legitimate", async () => {
    renderPage();

    const totalTokensCard = screen
      .getByText("Token 总量")
      .closest<HTMLElement>(".ant-card");
    expect(totalTokensCard).not.toBeNull();
    expect(within(totalTokensCard!).getByText("0")).toBeInTheDocument();
    expect(await screen.findByText("未有失事")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders usage and recent-audit task entries as real links", () => {
    apiMocks.useAuditStats.mockReturnValue(
      queryState({
        data: {
          ...emptyStats,
          per_edict: [
            {
              edict_id: "edict-usage",
              edict_title: "用量敕令",
              priority: "normal",
              token_budget: null,
              memorial_count: 1,
              prompt_tokens: 10,
              completion_tokens: 5,
              total_tokens: 15,
            },
          ],
          recent_audits: [
            {
              memorial_id: "memorial-1",
              edict_id: "edict-audit",
              edict_title: "审计敕令",
              verdict: "pass",
              reasons: [],
              rules_checked: 1,
              llm_reviewed: false,
              review_status: null,
              completed_at: "2026-07-31T08:00:00Z",
            },
          ],
        },
      }),
    );

    renderPage();

    expect(screen.getByRole("link", { name: "用量敕令" })).toHaveAttribute(
      "href",
      "/edicts/edict-usage",
    );
    expect(screen.getByRole("link", { name: "审计敕令" })).toHaveAttribute(
      "href",
      "/edicts/edict-audit",
    );
  });

  it("renders network-event task entries as real links", async () => {
    apiMocks.listNetworkEvents.mockResolvedValue([
      {
        event_id: "network-1",
        created_at: "2026-07-31T08:00:00Z",
        edict_id: "edict-network",
        edict_title: "联网敕令",
        tool: "web_fetch",
        host: "example.com",
        method: "GET",
        http_status: 200,
        bytes_out: 128,
        credential_name: null,
        cached: false,
        is_error: false,
        reason: null,
        provider: null,
        result_count: null,
        truncated: false,
      },
    ]);

    renderPage("/audit?tab=network");

    expect(await screen.findByRole("link", { name: "联网敕令" })).toHaveAttribute(
      "href",
      "/edicts/edict-network",
    );
  });

  it("shows rules loading instead of a successful empty table", () => {
    apiMocks.useAuditRules.mockReturnValue(queryState({ isLoading: true }));

    renderPage("/audit?tab=rules");

    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    expect(screen.queryByText("暂无规则")).not.toBeInTheDocument();
  });

  it("shows the rules empty state only after a successful response", async () => {
    renderPage("/audit?tab=rules");

    expect(await screen.findByText("暂无规则")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows failure attribution loading instead of reporting no failures", () => {
    apiMocks.getFailureDistribution.mockReturnValue(new Promise(() => {}));

    renderPage();

    const card = screen
      .getByText("失事缘由分布")
      .closest<HTMLElement>(".ant-card");
    expect(card).not.toBeNull();
    expect(within(card!).getByRole("status")).toHaveTextContent("正在加载");
    expect(within(card!).queryByText("未有失事")).not.toBeInTheDocument();
  });

  it("maps 403 stats failures to an explicit permission state", () => {
    apiMocks.useAuditStats.mockReturnValue(
      queryState({ error: problem(403, "permission-denied", "无权读取审计统计") }),
    );

    renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent("无权查看此内容");
    expect(screen.getByRole("alert")).toHaveTextContent("无权读取审计统计");
    expect(screen.queryByText("Token 总量")).not.toBeInTheDocument();
  });

  it("maps 503 rules failures to an explicit service state", () => {
    apiMocks.useAuditRules.mockReturnValue(
      queryState({ error: problem(503, "service-unavailable", "规则服务暂不可用") }),
    );

    renderPage("/audit?tab=rules");

    expect(screen.getByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("规则服务暂不可用");
    expect(screen.queryByText("暂无规则")).not.toBeInTheDocument();
  });

  it("shows policy stats loading instead of false zero decisions", () => {
    apiMocks.fetchPolicyStats.mockReturnValue(new Promise(() => {}));

    renderPage("/audit?tab=policy");

    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    expect(screen.queryByText("Allow")).not.toBeInTheDocument();
    expect(screen.queryByText("Deny")).not.toBeInTheDocument();
  });

  it("handles a rejected 401 policy request as an explicit permission state", async () => {
    apiMocks.fetchPolicyStats.mockRejectedValue(
      problem(401, "auth-required", "登录已失效"),
    );

    renderPage("/audit?tab=policy");

    expect(await screen.findByRole("alert")).toHaveTextContent("无权查看此内容");
    expect(screen.getByRole("alert")).toHaveTextContent("登录已失效");
    expect(screen.queryByText("Allow")).not.toBeInTheDocument();
  });

  it("shows a generic failure state without a false failure-attribution empty state", async () => {
    apiMocks.getFailureDistribution.mockRejectedValue(
      problem(500, "request-failed", "失事统计读取失败"),
    );

    renderPage();

    const card = screen
      .getByText("失事缘由分布")
      .closest<HTMLElement>(".ant-card");
    expect(card).not.toBeNull();
    await waitFor(() =>
      expect(within(card!).getByRole("alert")).toHaveTextContent("请求失败"),
    );
    expect(within(card!).getByRole("alert")).toHaveTextContent("失事统计读取失败");
    expect(within(card!).queryByText("未有失事")).not.toBeInTheDocument();
  });
});
