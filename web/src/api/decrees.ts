import apiClient from "./client";
import type {
  ApiResponse,
  Memorial,
  Decree,
  DecreeCreateRequest,
  PendingToolCall,
  ToolDecisionRequest,
  ToolDecisionResult,
} from "./types";

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

export async function fetchPendingToolCalls(): Promise<PendingToolCall[]> {
  const { data } = await apiClient.get<
    ApiResponse<{ items: PendingToolCall[] }>
  >("/approvals/pending_tool_calls");
  return data.data?.items ?? [];
}

export async function submitToolDecision(
  body: ToolDecisionRequest,
): Promise<ApiResponse<ToolDecisionResult>> {
  const { data } = await apiClient.post<ApiResponse<ToolDecisionResult>>(
    "/approvals/tool_decision",
    body,
  );
  return data;
}
