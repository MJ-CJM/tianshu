// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiProblem } from "../contracts/api";
import type { EvolutionCenterSnapshotV1 } from "../api/evolution";
import { useLocaleProvider } from "../hooks/useLocale";
import { EVOLUTION_CENTER_QUERY_KEY } from "../hooks/useEvolutionCenter";

const evolutionSource = vi.hoisted(() => ({
  calls: 0,
  result: undefined as EvolutionCenterSnapshotV1 | undefined,
  error: undefined as unknown,
}));
vi.mock("../api/evolution", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/evolution")>()),
  getEvolutionCenterSnapshot: () => {
    evolutionSource.calls += 1;
    if (evolutionSource.error !== undefined) throw evolutionSource.error;
    if (evolutionSource.result !== undefined) return Promise.resolve(evolutionSource.result);
    return new Promise(() => undefined);
  },
}));
vi.mock("../components/evolution/EvolutionPolicyPanel", () => ({
  default: () => <section aria-label="policy-panel">policy-panel</section>,
}));

import EvolutionCenterPage from "./EvolutionCenterPage";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const HASH_C = "c".repeat(64);
const GENERATION_A = `rg-${"a".repeat(32)}`;
const GENERATION_B = `rg-${"b".repeat(32)}`;

const NOT_ENABLED: EvolutionCenterSnapshotV1 = {
  schema_version: 1,
  status: "not_enabled",
  reason_code: "s5_governed_evolution_not_enabled",
  routing_enabled: true,
  active_generation: null,
  last_good_generation: null,
  candidates: [],
  routing: [],
  last_gate_hash: null,
};

const ENABLED_EMPTY: EvolutionCenterSnapshotV1 = {
  ...NOT_ENABLED,
  status: "enabled",
  reason_code: "enabled_no_candidates",
  active_generation: GENERATION_B,
  last_good_generation: GENERATION_A,
};

const DEGRADED_EMPTY: EvolutionCenterSnapshotV1 = {
  ...NOT_ENABLED,
  status: "degraded",
  reason_code: "evolution_source_degraded",
  active_generation: GENERATION_B,
  last_good_generation: GENERATION_A,
};

const FIXTURE: EvolutionCenterSnapshotV1 = {
  schema_version: 1,
  status: "enabled",
  reason_code: "minimum_samples_blocking",
  routing_enabled: true,
  active_generation: GENERATION_B,
  last_good_generation: GENERATION_A,
  candidates: [
    {
      candidate_id: "candidate-skill-7",
      kind: "skill",
      version: 7,
      lifecycle: "canary",
      artifact_hash: HASH_A,
      promotion_allowed: false,
      rollback_state: "ready",
      gates: [
        {
          code: "minimum_samples",
          status: "failed",
          blocking: true,
          current: 18,
          required: 50,
          evidence_bundle_id: "evidence:gate-samples",
          evidence_hash: HASH_B,
        },
      ],
    },
  ],
  routing: [
    {
      candidate_id: "candidate-skill-7",
      subject_key: "skill:reviewer",
      routing_version: 3,
      allocation_percent: 10,
      champion_assignment_count: 82,
      challenger_assignment_count: 18,
    },
  ],
  last_gate_hash: HASH_C,
};

const DEGRADED_FIXTURE: EvolutionCenterSnapshotV1 = {
  ...FIXTURE,
  status: "degraded",
  reason_code: "gate_evaluation_degraded",
};

function problem(status: number, code: string, message: string): ApiProblem {
  return { status, code, message, correlationId: "corr-evolution", retryable: status >= 500 };
}

function LocaleControls() {
  const locale = useLocaleProvider();
  return (
    <>
      <button type="button" onClick={() => locale.setLocale("en")}>English</button>
      <button type="button" onClick={() => locale.setLocale("zh-modern")}>modern</button>
      <button type="button" onClick={() => locale.setLocale("zh-classic")}>classic</button>
    </>
  );
}

function renderPage(queryClient?: QueryClient, showLocaleControls = false) {
  const client =
    queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/evolution"]}>
        {showLocaleControls ? <LocaleControls /> : null}
        <EvolutionCenterPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  evolutionSource.calls = 0;
  evolutionSource.result = undefined;
  evolutionSource.error = undefined;
});
afterEach(cleanup);

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("authoritative Evolution Center snapshot", () => {
  it("shows loading while the single snapshot request is pending", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "演化中心" })).toBeInTheDocument();
    expect(screen.getAllByText("实验")).toHaveLength(2);
    expect(
      screen.getByText("查看并治理 Skill 候选之门禁、灰度分流、晋升与回滚证据。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("非 Skill 激活仍闭；系统不会自行晋升候选。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "正在加载" })).toBeInTheDocument();
    expect(evolutionSource.calls).toBe(1);
  });

  it("shows the exact pre-S5 reason without canary or promotion claims", async () => {
    evolutionSource.result = NOT_ENABLED;
    renderPage();

    expect(await screen.findByText("S5 受治理演化尚未启用；当前没有候选、分流或门禁结果。")).toBeInTheDocument();
    expect(screen.getByText("s5_governed_evolution_not_enabled")).toBeInTheDocument();
    expect(
      screen.queryByText(/Canary 已启用|自动晋升|真实流量/),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders success-empty distinctly for an enabled future snapshot with no candidates", async () => {
    evolutionSource.result = ENABLED_EMPTY;
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "已启用，尚无候选" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "演化服务已就绪；待建立受治理 Skill 候选，此处即呈门禁、分流与回滚状态。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "暂无数据" })).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "进程运行世代" })).toBeInTheDocument();
    expect(screen.getByText(GENERATION_B)).toBeInTheDocument();
    expect(screen.getByText(GENERATION_A)).toBeInTheDocument();
  });

  it("renders degraded status and reason even when the snapshot has no candidates", async () => {
    evolutionSource.result = DEGRADED_EMPTY;
    renderPage();

    expect(await screen.findByRole("heading", { name: "演化状态" })).toBeInTheDocument();
    expect(screen.getByText("降级")).toBeInTheDocument();
    expect(screen.getByText("evolution_source_degraded")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "暂无数据" })).not.toBeInTheDocument();
  });

  it("shows an explicit routing-disabled boundary without hiding governed data", async () => {
    evolutionSource.result = { ...FIXTURE, routing_enabled: false };
    renderPage();

    expect(await screen.findByText("candidate-skill-7")).toBeInTheDocument();
    expect(screen.getByText("新试行分流已闭")).toBeInTheDocument();
    expect(
      screen.getByText("新行皆守正本；旧有成案仍循原分配，不复分桶。"),
    ).toBeInTheDocument();
  });

  it("keeps degraded status and reason visible beside candidate data", async () => {
    evolutionSource.result = DEGRADED_FIXTURE;
    renderPage();

    expect(await screen.findByText("candidate-skill-7")).toBeInTheDocument();
    expect(screen.getByText("降级")).toBeInTheDocument();
    expect(screen.getByText("gate_evaluation_degraded")).toBeInTheDocument();
  });

  it("labels governed data as the bounded Lean Core Gate without a full G4 claim", async () => {
    evolutionSource.result = FIXTURE;
    renderPage();

    expect(await screen.findByText("Lean Core Gate")).toBeInTheDocument();
    expect(screen.queryByText(/G4 passed/i)).not.toBeInTheDocument();
  });

  it("indexes validated routing identities instead of performing ambiguous array lookup", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/pages/EvolutionCenterPage.tsx"),
      "utf8",
    );

    expect(source).toContain("new Map(");
    expect(source).not.toContain("snapshot.routing.find(");
  });

  it("renders fixture gates hashes assignments rollback and no mutation controls", async () => {
    evolutionSource.result = FIXTURE;
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("candidate-skill-7")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "进程运行世代" })).toBeInTheDocument();
    expect(screen.getByText(GENERATION_B)).toBeInTheDocument();
    expect(screen.getByText(GENERATION_A)).toBeInTheDocument();
    expect(screen.getByText(HASH_A)).toBeInTheDocument();
    expect(screen.getByText(HASH_B)).toBeInTheDocument();
    expect(screen.getByText(HASH_C)).toBeInTheDocument();
    expect(screen.getByText("18 / 50")).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getAllByText("18")).toHaveLength(1);
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    const evidence = screen.getByRole("link", { name: "查看门禁证据" });
    expect(evidence).toHaveAttribute(
      "href",
      "/api/evidence/evidence%3Agate-samples/download",
    );
    await user.tab();
    expect(document.activeElement).toBe(evidence);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("scrolls to and highlights a candidate card addressed by the URL hash", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    window.history.replaceState(
      null,
      "",
      "/evolution#candidate-candidate-skill-7",
    );
    evolutionSource.result = FIXTURE;
    renderPage();

    const candidateTitle = await screen.findByText("candidate-skill-7");
    const candidateCard = candidateTitle.closest("article");
    expect(candidateCard).toHaveAttribute("id", "candidate-candidate-skill-7");
    expect(candidateCard).toHaveAttribute("data-hash-targeted", "true");
    expect(candidateCard).toHaveStyle({
      boxShadow: "0 0 0 2px var(--ts-color-accent)",
    });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" }));
  });

  it("keeps cached fixture data visible when refresh becomes stale", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(EVOLUTION_CENTER_QUERY_KEY, FIXTURE);
    evolutionSource.error = problem(
      500,
      "evolution-refresh-failed",
      "演化快照刷新失败",
    );
    renderPage(client);

    expect(await screen.findByRole("heading", { name: "数据可能已过期" })).toBeInTheDocument();
    expect(screen.getByText("candidate-skill-7")).toBeInTheDocument();
    expect(screen.getByText("演化快照刷新失败")).toBeInTheDocument();
  });

  it("renders generic error without fabricated disabled data", async () => {
    evolutionSource.error = problem(500, "evolution-failed", "演化快照读取失败");
    renderPage();

    expect(await screen.findByRole("heading", { name: "请求失败" })).toBeInTheDocument();
    expect(screen.queryByText("尚未启用")).not.toBeInTheDocument();
  });

  it("renders permission denied distinctly", async () => {
    evolutionSource.error = problem(403, "permission-denied", "当前身份无权查看演化中心");
    renderPage();

    expect(await screen.findByRole("heading", { name: "无权查看此内容" })).toBeInTheDocument();
    expect(screen.getByText("关联标识: corr-evolution")).toBeInTheDocument();
  });

  it("renders service unavailable distinctly", async () => {
    evolutionSource.error = problem(
      503,
      "evolution_center_unavailable",
      "演化中心数据源暂不可用",
    );
    renderPage();

    expect(await screen.findByRole("heading", { name: "服务暂不可用" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("uses all three locales for Evolution Center truth copy", async () => {
    evolutionSource.result = NOT_ENABLED;
    const user = userEvent.setup();
    renderPage(undefined, true);

    await user.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByRole("heading", { name: "Evolution Center" })).toBeInTheDocument();
    expect(screen.getByText("Governed evolution is not enabled before S5; there are no candidates, routing assignments, or gate results.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "modern" }));
    expect(screen.getByRole("heading", { name: "演化中心" })).toBeInTheDocument();
    expect(screen.getByText("S5 受治理演化尚未启用；当前没有候选、分流或门禁结果。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "classic" }));
  });
});
