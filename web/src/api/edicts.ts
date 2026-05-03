import apiClient from "./client";
import type { AcceptanceCriteria, ApiResponse, Edict, EdictCreateRequest, EdictRuntime, EdictStatus, EdictUpdateRequest, Memorial, EdictEvent, OuterLoopIteration, SupervisionReport } from "./types";

export async function getOuterLoopIterations(edictId: string): Promise<ApiResponse<OuterLoopIteration[]>> {
  const { data } = await apiClient.get<ApiResponse<OuterLoopIteration[]>>(
    `/edicts/${edictId}/iterations`,
  );
  return data;
}

export async function getSupervisionReports(edictId: string): Promise<SupervisionReport[]> {
  try {
    const { data } = await apiClient.get<ApiResponse<SupervisionReport[]>>(
      `/edicts/${edictId}/supervision-reports`,
    );
    return data.data ?? [];
  } catch {
    return [];
  }
}

/** @deprecated 用 getSupervisionReports 复数版（多监督官） */
export async function getSupervisionReport(edictId: string): Promise<SupervisionReport | null> {
  const list = await getSupervisionReports(edictId);
  return list.length > 0 ? list[0]! : null;
}

export async function createEdict(body: EdictCreateRequest): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.post<ApiResponse<Edict>>(
    "/edicts",
    body,
  );
  return data;
}

export async function listEdicts(params: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<ApiResponse<Edict[]>> {
  const { data } = await apiClient.get<ApiResponse<Edict[]>>("/edicts", {
    params,
  });
  return data;
}

export async function getEdict(edictId: string): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.get<ApiResponse<Edict>>(
    `/edicts/${edictId}`,
  );
  return data;
}

export async function getEdictMemorial(
  edictId: string,
): Promise<ApiResponse<Memorial>> {
  const { data } = await apiClient.get<ApiResponse<Memorial>>(
    `/edicts/${edictId}/memorial`,
  );
  return data;
}

export async function getEdictMemorials(
  edictId: string,
): Promise<ApiResponse<Memorial[]>> {
  const { data } = await apiClient.get<ApiResponse<Memorial[]>>(
    `/edicts/${edictId}/memorials`,
  );
  return data;
}

export async function getEdictEvents(
  edictId: string,
): Promise<ApiResponse<EdictEvent[]>> {
  const { data } = await apiClient.get<ApiResponse<EdictEvent[]>>(
    `/edicts/${edictId}/events`,
  );
  return data;
}

export interface FollowUpRequest {
  instruction: string;
  context?: string;
  /** 本次 follow-up 单独覆盖 edict.runtime（仅含填写字段） */
  runtime_override?: Partial<EdictRuntime>;
  /** 本次 follow-up 单独覆盖 edict.acceptance（整体替换） */
  acceptance_override?: AcceptanceCriteria;
}

export async function followUpEdict(
  edictId: string,
  body: FollowUpRequest,
): Promise<ApiResponse<Memorial>> {
  const { data } = await apiClient.post<ApiResponse<Memorial>>(
    `/edicts/${edictId}/follow-up`,
    body,
  );
  return data;
}

export async function updateEdict(
  edictId: string,
  body: EdictUpdateRequest,
): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.patch<ApiResponse<Edict>>(
    `/edicts/${edictId}`,
    body,
  );
  return data;
}

export async function deleteEdict(
  edictId: string,
): Promise<ApiResponse<{ id: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ id: string }>>(
    `/edicts/${edictId}`,
  );
  return data;
}

export async function approvePlan(edictId: string): Promise<ApiResponse<{ status: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ status: string }>>(
    `/edicts/${edictId}/plan/approve`,
  );
  return data;
}

export async function rejectPlan(edictId: string): Promise<ApiResponse<{ status: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ status: string }>>(
    `/edicts/${edictId}/plan/reject`,
  );
  return data;
}

export async function updateEdictStatus(
  edictId: string,
  status: EdictStatus,
): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.patch<ApiResponse<Edict>>(
    `/edicts/${edictId}/status`,
    { status },
  );
  return data;
}

export async function pauseEdict(
  edictId: string,
): Promise<ApiResponse<{ id: string; lifecycle_phase: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ id: string; lifecycle_phase: string }>>(
    `/edicts/${edictId}/pause`,
  );
  return data;
}

export async function resumeEdict(
  edictId: string,
): Promise<ApiResponse<{ id: string; lifecycle_phase: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ id: string; lifecycle_phase: string }>>(
    `/edicts/${edictId}/resume`,
  );
  return data;
}
