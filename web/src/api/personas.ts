import apiClient from "./client";
import type {
  ApiResponse,
  PersonaInfo,
  PersonaMetrics,
  PersonaCreateRequest,
  PersonaUpdateRequest,
} from "./types";

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

export async function createPersona(
  body: PersonaCreateRequest,
): Promise<ApiResponse<PersonaInfo>> {
  const { data } = await apiClient.post<ApiResponse<PersonaInfo>>(
    "/personas",
    body,
  );
  return data;
}

export async function updatePersona(
  id: string,
  body: PersonaUpdateRequest,
): Promise<ApiResponse<PersonaInfo>> {
  const { data } = await apiClient.put<ApiResponse<PersonaInfo>>(
    `/personas/${id}`,
    body,
  );
  return data;
}

export async function deletePersona(
  id: string,
): Promise<ApiResponse<{ id: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ id: string }>>(
    `/personas/${id}`,
  );
  return data;
}
