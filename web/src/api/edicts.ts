import apiClient from "./client";
import type { ApiResponse, Edict, EdictCreateRequest, EdictStatus, EdictUpdateRequest, Memorial, EdictEvent, OuterLoopIteration } from "./types";

export async function getOuterLoopIterations(edictId: string): Promise<ApiResponse<OuterLoopIteration[]>> {
  const { data } = await apiClient.get<ApiResponse<OuterLoopIteration[]>>(
    `/edicts/${edictId}/iterations`,
  );
  return data;
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

export async function followUpEdict(
  edictId: string,
  body: { instruction: string; context?: string },
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
