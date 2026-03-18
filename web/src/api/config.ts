import apiClient from "./client";
import type { ApiResponse, LLMConfig, LLMConfigUpdateRequest } from "./types";

export async function getConfig(): Promise<LLMConfig> {
  const { data } = await apiClient.get<ApiResponse<LLMConfig>>("/config");
  return data.data!;
}

export async function updateConfig(
  req: LLMConfigUpdateRequest,
): Promise<LLMConfig> {
  const { data } = await apiClient.put<ApiResponse<LLMConfig>>("/config", req);
  return data.data!;
}
