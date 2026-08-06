import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createModelProvider,
  deleteModelProvider,
  getCatalogStatus,
  listModelProviderProfiles,
  listModelProviders,
  listProviderModels,
  refreshCatalog,
  setModelProviderKey,
  testModelProvider,
  updateModelProvider,
} from "../api/modelProviders";
import type {
  ModelProviderCreateRequest,
  ModelProviderUpdateRequest,
} from "../api/types";

const MODEL_PROVIDERS_KEY = ["model-providers"];
const PROFILES_KEY = ["model-provider-profiles"];
const CATALOG_STATUS_KEY = ["model-catalog-status"];
const CONFIGS_KEY = ["configs"];

export function useModelProviderProfiles() {
  return useQuery({
    queryKey: PROFILES_KEY,
    queryFn: listModelProviderProfiles,
  });
}

export function useModelProviders() {
  return useQuery({
    queryKey: MODEL_PROVIDERS_KEY,
    queryFn: listModelProviders,
  });
}

export function useCreateModelProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: ModelProviderCreateRequest) => createModelProvider(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MODEL_PROVIDERS_KEY });
      // 创建可携带 api_key，configs 的 masked key 可能随之变化
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });
}

export function useUpdateModelProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      req,
    }: {
      id: string;
      req: ModelProviderUpdateRequest;
    }) => updateModelProvider(id, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MODEL_PROVIDERS_KEY });
    },
  });
}

export function useSetModelProviderKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, apiKey }: { id: string; apiKey: string }) =>
      setModelProviderKey(id, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MODEL_PROVIDERS_KEY });
      // configs 的 masked key 会跟着变
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });
}

export function useDeleteModelProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteModelProvider(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MODEL_PROVIDERS_KEY });
    },
  });
}

/** providerId 为空时 disabled（不发请求） */
export function useProviderModels(providerId?: string, q?: string) {
  return useQuery({
    queryKey: [...MODEL_PROVIDERS_KEY, providerId ?? "", "models", q ?? ""],
    queryFn: () => listProviderModels(providerId!, q),
    enabled: !!providerId,
  });
}

export function useTestModelProvider() {
  return useMutation({
    mutationFn: ({ id, model }: { id: string; model: string }) =>
      testModelProvider(id, model),
  });
}

export function useCatalogStatus() {
  return useQuery({
    queryKey: CATALOG_STATUS_KEY,
    queryFn: getCatalogStatus,
  });
}

export function useRefreshCatalog() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => refreshCatalog(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CATALOG_STATUS_KEY });
      // 目录刷新后各 provider 的模型列表（前缀 model-providers）需要重取
      queryClient.invalidateQueries({ queryKey: MODEL_PROVIDERS_KEY });
    },
  });
}
