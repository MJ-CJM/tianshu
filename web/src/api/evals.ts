import apiClient from "./client";
import type {
  ApiResponse,
  EvalRun,
  EvalRunBrief,
  EvalSet,
  FailureDistributionItem,
} from "./types";

export async function listEvalRuns(limit = 50): Promise<ApiResponse<EvalRunBrief[]>> {
  const { data } = await apiClient.get<ApiResponse<EvalRunBrief[]>>("/evals/runs", {
    params: { limit },
  });
  return data;
}

export async function getEvalRun(runId: string): Promise<ApiResponse<EvalRun>> {
  const { data } = await apiClient.get<ApiResponse<EvalRun>>(`/evals/runs/${runId}`);
  return data;
}

export async function listEvalSets(): Promise<ApiResponse<EvalSet[]>> {
  const { data } = await apiClient.get<ApiResponse<EvalSet[]>>("/evals/sets");
  return data;
}

export async function getFailureDistribution(
  days?: number,
): Promise<ApiResponse<FailureDistributionItem[]>> {
  const { data } = await apiClient.get<ApiResponse<FailureDistributionItem[]>>(
    "/evals/failure-distribution",
    { params: days ? { days } : {} },
  );
  return data;
}
