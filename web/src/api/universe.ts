import apiClient from "./client";
import type { ApiResponse, Universe } from "./types";

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

export async function switchUniverse(id: string): Promise<ApiResponse<Universe>> {
  const { data } = await apiClient.post<ApiResponse<Universe>>(
    `/universes/${id}/switch`,
    {},
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

export async function diffUniverses(
  a: string,
  b: string,
): Promise<ApiResponse<{ personas: unknown; skills: unknown; config: unknown }>> {
  const { data } = await apiClient.get<
    ApiResponse<{ personas: unknown; skills: unknown; config: unknown }>
  >("/universes/_diff", { params: { a, b } });
  return data;
}
