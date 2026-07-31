import type { ApiResponse, CostSummary, CostRecord, BudgetStatus } from "./types";
import apiClient from "./client";

export async function getCostSummary(
  period?: string,
  edictId?: string,
): Promise<CostSummary> {
  const params = new URLSearchParams();
  if (period) params.set("period", period);
  if (edictId) params.set("edict_id", edictId);
  const qs = params.toString();
  const { data } = await apiClient.get<ApiResponse<CostSummary>>(
    `/cost/summary${qs ? `?${qs}` : ""}`,
  );
  return data.data!;
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
  const { data } = await apiClient.get<ApiResponse<CostRecord[]>>(
    `/cost/records?${params}`,
  );
  return {
    records: data.data ?? [],
    total: data.metadata?.total ?? 0,
  };
}

export async function getCostBudget(
  scope = "global",
): Promise<BudgetStatus | null> {
  const { data } = await apiClient.get<ApiResponse<BudgetStatus | null>>(
    `/cost/budget?scope=${encodeURIComponent(scope)}`,
  );
  return data.data;
}

export async function setCostBudget(
  scope: string,
  budgetCny: number,
  period = "monthly",
): Promise<void> {
  await apiClient.put("/cost/budget", {
    scope,
    budget_cny: budgetCny,
    period,
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
  const { data } = await apiClient.get<
    ApiResponse<{ summary: CostSummary; records: CostRecord[] }>
  >(`/cost/export${qs ? `?${qs}` : ""}`);
  return data.data!;
}
