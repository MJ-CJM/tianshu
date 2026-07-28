import apiClient from "./client";
import type {
  ApiResponse,
  PersonaCreateRequest,
  PersonaImportDraft,
  PersonaImportSourceKind,
  PersonaInfo,
  PersonaMetrics,
  PersonaUpdateRequest,
} from "./types";

/** 从外部(openclaw/hermes)读配置作导入预览(只读,不落库)。path 省略则服务端探测默认目录。 */
export async function previewPersonaImport(
  source: PersonaImportSourceKind,
  path?: string,
): Promise<PersonaImportDraft> {
  const { data } = await apiClient.post<ApiResponse<PersonaImportDraft>>(
    "/personas/import/preview",
    { source, path },
  );
  return data.data!;
}

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

export async function regeneratePersonaIdentity(
  id: string,
): Promise<ApiResponse<{ id: string; soul_path: string; role_path: string }>> {
  const { data } = await apiClient.post<
    ApiResponse<{ id: string; soul_path: string; role_path: string }>
  >(`/personas/${id}/regenerate-identity`);
  return data;
}
