import apiClient from "./client";
import type {
  ApiResponse,
  CatalogModelEntry,
  CatalogStatus,
  ConnectivityTestResult,
  ModelProviderCreateRequest,
  ModelProviderProfile,
  ModelProviderUpdateRequest,
  ModelProviderView,
} from "./types";

// --- 统一模型注册表（Model Provider Registry） ---

export async function listModelProviderProfiles(): Promise<
  ModelProviderProfile[]
> {
  const { data } = await apiClient.get<ApiResponse<ModelProviderProfile[]>>(
    "/model-providers/profiles",
  );
  return data.data ?? [];
}

export async function listModelProviders(): Promise<ModelProviderView[]> {
  const { data } =
    await apiClient.get<ApiResponse<ModelProviderView[]>>("/model-providers");
  return data.data ?? [];
}

export async function createModelProvider(
  req: ModelProviderCreateRequest,
): Promise<ModelProviderView> {
  const { data } = await apiClient.post<ApiResponse<ModelProviderView>>(
    "/model-providers",
    req,
  );
  return data.data!;
}

export async function updateModelProvider(
  id: string,
  req: ModelProviderUpdateRequest,
): Promise<ModelProviderView> {
  const { data } = await apiClient.put<ApiResponse<ModelProviderView>>(
    `/model-providers/${encodeURIComponent(id)}`,
    req,
  );
  return data.data!;
}

/** 更新 key（空串 = 清除，回落 profile env） */
export async function setModelProviderKey(
  id: string,
  apiKey: string,
): Promise<ModelProviderView> {
  const { data } = await apiClient.put<ApiResponse<ModelProviderView>>(
    `/model-providers/${encodeURIComponent(id)}/key`,
    { api_key: apiKey },
  );
  return data.data!;
}

export async function deleteModelProvider(id: string): Promise<void> {
  await apiClient.delete(`/model-providers/${encodeURIComponent(id)}`);
}

export async function listProviderModels(
  id: string,
  q?: string,
): Promise<CatalogModelEntry[]> {
  const { data } = await apiClient.get<ApiResponse<CatalogModelEntry[]>>(
    `/model-providers/${encodeURIComponent(id)}/models`,
    { params: q ? { q } : undefined },
  );
  return data.data ?? [];
}

export async function testModelProvider(
  id: string,
  model: string,
): Promise<ConnectivityTestResult> {
  const { data } = await apiClient.post<ApiResponse<ConnectivityTestResult>>(
    `/model-providers/${encodeURIComponent(id)}/test`,
    { model },
  );
  return data.data!;
}

export async function getCatalogStatus(): Promise<CatalogStatus> {
  const { data } = await apiClient.get<ApiResponse<CatalogStatus>>(
    "/model-catalog/status",
  );
  return data.data!;
}

export async function refreshCatalog(): Promise<CatalogStatus> {
  const { data } = await apiClient.post<ApiResponse<CatalogStatus>>(
    "/model-catalog/refresh",
  );
  return data.data!;
}
