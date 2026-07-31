import apiClient from "./client";
import type { ApiResponse, EdictSchedule, SchedulerJob, SchedulerRun } from "./types";

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

export async function pauseSchedulerJob(jobId: string) {
  const { data } = await apiClient.post<ApiResponse<{ job_id: string; status: string }>>(
    `/scheduler/jobs/${jobId}/pause`,
  );
  return data;
}

export async function resumeSchedulerJob(jobId: string) {
  const { data } = await apiClient.post<ApiResponse<{ job_id: string; status: string }>>(
    `/scheduler/jobs/${jobId}/resume`,
  );
  return data;
}

export async function runSchedulerJobNow(jobId: string) {
  const { data } = await apiClient.post<ApiResponse<{ job_id: string; status: string }>>(
    `/scheduler/jobs/${jobId}/run-now`,
    undefined,
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
  return data;
}

export async function updateSchedulerJob(jobId: string, schedule: EdictSchedule) {
  const { data } = await apiClient.patch<ApiResponse<{ job_id: string }>>(
    `/scheduler/jobs/${jobId}`,
    { schedule },
  );
  return data;
}

export async function listSchedulerJobRuns(jobId: string, limit = 20) {
  const { data } = await apiClient.get<ApiResponse<SchedulerRun[]>>(
    `/scheduler/jobs/${jobId}/runs`,
    { params: { limit } },
  );
  return data;
}
