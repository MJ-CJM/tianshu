import apiClient from "./client";
import type { ApiResponse, Memorial, Decree, DecreeCreateRequest } from "./types";

export async function createDecree(
  body: DecreeCreateRequest,
): Promise<ApiResponse<Decree>> {
  const { data } = await apiClient.post<ApiResponse<Decree>>(
    "/decrees",
    body,
  );
  return data;
}

export async function listNeedsReview(params?: {
  limit?: number;
  offset?: number;
}): Promise<ApiResponse<Memorial[]>> {
  const { data } = await apiClient.get<ApiResponse<Memorial[]>>("/memorials", {
    params: { status: "needs_review", ...params },
  });
  return data;
}
