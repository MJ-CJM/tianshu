import apiClient from "./client";
import type { ApiResponse, Memorial } from "./types";

export async function listMemorials(params: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ApiResponse<Memorial[]>> {
  const { data } = await apiClient.get<ApiResponse<Memorial[]>>("/memorials", {
    params,
  });
  return data;
}

export async function getMemorial(
  memorialId: string,
): Promise<ApiResponse<Memorial>> {
  const { data } = await apiClient.get<ApiResponse<Memorial>>(
    `/memorials/${memorialId}`,
  );
  return data;
}
