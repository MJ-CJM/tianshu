import apiClient from "./client";
import type { ApiResponse, Universe, VariantEvalRun } from "./types";

export interface TaiyiMemorial {
  type: string;
  title: string;
  summary: string;
  findings: Array<{ target: string; hypothesis: string }>;
  count: number;
}

export interface TaiyiReportState {
  status: "not_generated" | "ready";
  report: TaiyiMemorial | null;
  generated_at: string | null;
}

export async function listUniverses(): Promise<ApiResponse<Universe[]>> {
  const { data } = await apiClient.get<ApiResponse<Universe[]>>("/universes");
  return data;
}

export async function branchUniverse(
  id: string,
  name: string,
  description = "",
): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(
    `/universes/${id}/branch`,
    { name, description },
  );
  return data;
}

export async function archiveUniverse(id: string): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(
    `/universes/${id}/archive`,
    {},
  );
  return data;
}

export async function restoreUniverse(id: string): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(
    `/universes/${id}/restore`,
    {},
  );
  return data;
}

export async function enableParallelUniverse(): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>("/universes/enable", {});
  return data;
}

export async function getUniverseStatus(): Promise<ApiResponse<{ enabled: boolean }>> {
  const { data } = await apiClient.get<ApiResponse<{ enabled: boolean }>>("/universes/_status");
  return data;
}

export async function getTaiyiReport(): Promise<ApiResponse<TaiyiReportState>> {
  const { data } = await apiClient.get<ApiResponse<TaiyiReportState>>(
    "/universes/taiyi/report",
  );
  return data;
}

export async function generateTaiyiReport(): Promise<ApiResponse<TaiyiReportState>> {
  const { data } = await apiClient.post<ApiResponse<TaiyiReportState>>(
    "/universes/taiyi/report",
    {},
  );
  return data;
}

export async function submitUniverseFeedback(
  memorialId: string, score: number,
): Promise<ApiResponse<{ universe_id: string | null; score: number }>> {
  const { data } = await apiClient.post<ApiResponse<{ universe_id: string | null; score: number }>>(
    "/universes/feedback", { memorial_id: memorialId, score },
  );
  return data;
}

export async function triggerEvolve(): Promise<ApiResponse<Record<string, unknown>>> {
  const { data } = await apiClient.post<ApiResponse<Record<string, unknown>>>("/universes/evolve", {});
  return data;
}

export async function diffUniverses(
  a: string,
  b: string,
): Promise<ApiResponse<{ personas: unknown; skills: unknown; config: unknown }>> {
  const { data } = await apiClient.get<
    ApiResponse<{ personas: unknown; skills: unknown; config: unknown }>
  >("/universes/_diff", { params: { a, b } });
  return data;
}

export async function proposeCodeVariant(
  targetPath: string, hypothesis: string, parentId?: string,
): Promise<ApiResponse<Record<string, unknown>>> {
  const { data } = await apiClient.post<ApiResponse<Record<string, unknown>>>(
    "/universes/propose-code",
    { target_path: targetPath, hypothesis, parent_id: parentId },
  );
  return data;
}

export async function proposeAutoCode(): Promise<ApiResponse<Record<string, unknown>>> {
  const { data } = await apiClient.post<ApiResponse<Record<string, unknown>>>(
    "/universes/propose-auto",
  );
  return data;
}

export async function getCodeDiff(id: string): Promise<ApiResponse<{ diff: string }>> {
  const { data } = await apiClient.get<ApiResponse<{ diff: string }>>(
    `/universes/${id}/code-diff`,
  );
  return data;
}

export async function listEvalRuns(id: string): Promise<ApiResponse<VariantEvalRun[]>> {
  const { data } = await apiClient.get<ApiResponse<VariantEvalRun[]>>(
    `/universes/${id}/eval-runs`,
  );
  return data;
}

export async function deleteUniverse(id: string): Promise<ApiResponse<{ id: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ id: string }>>(`/universes/${id}`);
  return data;
}
