import apiClient from "./client";
import type { ApiResponse, AuditStats } from "./types";

export async function getAuditStats(): Promise<ApiResponse<AuditStats>> {
  const { data } = await apiClient.get<ApiResponse<AuditStats>>("/audit/stats");
  return data;
}
