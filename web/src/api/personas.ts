import apiClient from "./client";
import type { ApiResponse, PersonaInfo, PersonaMetrics } from "./types";

export async function listPersonas(): Promise<ApiResponse<PersonaInfo[]>> {
  const { data } = await apiClient.get<ApiResponse<PersonaInfo[]>>("/personas");
  return data;
}

export async function getPersonaMetrics(
  id: string,
): Promise<ApiResponse<PersonaMetrics>> {
  const { data } = await apiClient.get<ApiResponse<PersonaMetrics>>(
    `/personas/${id}/metrics`,
  );
  return data;
}
