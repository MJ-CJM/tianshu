import apiClient from "./client";
import type { ApiResponse, SchedulerJob } from "./types";

export async function listSchedulerJobs(): Promise<ApiResponse<SchedulerJob[]>> {
  const { data } = await apiClient.get<ApiResponse<SchedulerJob[]>>("/scheduler/jobs");
  return data;
}

export async function cancelSchedulerJob(
  jobId: string,
): Promise<ApiResponse<{ job_id: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ job_id: string }>>(
    `/scheduler/jobs/${jobId}`,
  );
  return data;
}
