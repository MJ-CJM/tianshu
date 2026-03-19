import type { ApiResponse, CostSummary, CostRecord, BudgetStatus } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, init);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getCostSummary(
  period?: string,
  edictId?: string,
): Promise<CostSummary> {
  const params = new URLSearchParams();
  if (period) params.set("period", period);
  if (edictId) params.set("edict_id", edictId);
  const qs = params.toString();
  const resp = await fetchJson<ApiResponse<CostSummary>>(
    `/api/cost/summary${qs ? `?${qs}` : ""}`,
  );
  return resp.data!;
}

export async function getCostRecords(
  edictId?: string,
  limit = 50,
  offset = 0,
): Promise<{ records: CostRecord[]; total: number }> {
  const params = new URLSearchParams();
  if (edictId) params.set("edict_id", edictId);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  const resp = await fetchJson<ApiResponse<CostRecord[]>>(
    `/api/cost/records?${params}`,
  );
  return {
    records: resp.data ?? [],
    total: resp.metadata?.total ?? 0,
  };
}

export async function getCostBudget(
  scope = "global",
): Promise<BudgetStatus | null> {
  const resp = await fetchJson<ApiResponse<BudgetStatus | null>>(
    `/api/cost/budget?scope=${scope}`,
  );
  return resp.data;
}

export async function setCostBudget(
  scope: string,
  budgetCny: number,
  period = "monthly",
): Promise<void> {
  await fetchJson(`/api/cost/budget`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope, budget_cny: budgetCny, period }),
  });
}

export async function exportCostRecords(
  period?: string,
  edictId?: string,
): Promise<{ summary: CostSummary; records: CostRecord[] }> {
  const params = new URLSearchParams();
  if (period) params.set("period", period);
  if (edictId) params.set("edict_id", edictId);
  const qs = params.toString();
  const resp = await fetchJson<
    ApiResponse<{ summary: CostSummary; records: CostRecord[] }>
  >(`/api/cost/export${qs ? `?${qs}` : ""}`);
  return resp.data!;
}
