import apiClient from "./client";
import type { ApiResponse, Edict, EdictStatus, Memorial, EdictEvent } from "./types";

export async function createEdict(body: {
  goal: string;
  context?: string;
}): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.post<ApiResponse<Edict>>(
    "/edicts",
    body,
  );
  return data;
}

export async function listEdicts(params: {
  status?: string;
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
