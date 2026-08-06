import apiClient from "./client";
import type { ApiResponse, Memorial, PendingToolCall } from "./types";

export async function listNeedsReview(params?: {
  limit?: number;
  offset?: number;
}): Promise<ApiResponse<Memorial[]>> {
  const { data } = await apiClient.get<ApiResponse<Memorial[]>>("/memorials", {
    params: { status: "needs_review", ...params },
  });
  return data;
}

export async function fetchPendingToolCalls(): Promise<PendingToolCall[]> {
  const { data } = await apiClient.get<
    ApiResponse<{ items: PendingToolCall[] }>
  >("/approvals/pending_tool_calls");
  return data.data?.items ?? [];
}
