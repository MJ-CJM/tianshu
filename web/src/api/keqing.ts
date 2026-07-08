import apiClient from "./client";
import type { ApiResponse, ShadowSnapshot } from "./types";

export async function listKeqingAgents(): Promise<ApiResponse<string[]>> {
  const { data } = await apiClient.get<ApiResponse<string[]>>("/keqing/agents");
  return data;
}

export async function listSnapshots(
  edictId: string,
): Promise<ApiResponse<ShadowSnapshot[]>> {
  const { data } = await apiClient.get<ApiResponse<ShadowSnapshot[]>>(
    `/edicts/${edictId}/snapshots`,
  );
  return data;
}

export async function revertSnapshot(
  edictId: string,
  sha: string,
): Promise<ApiResponse<{ reverted_to: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ reverted_to: string }>>(
    `/edicts/${edictId}/snapshots/revert`,
    { sha },
  );
  return data;
}
