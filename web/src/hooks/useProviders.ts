import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getProviders,
  createProvider,
  updateProvider,
  deleteProvider,
  getPlugins,
} from "../api/providers";
import type { ProviderInfo } from "../api/types";

export function useProviders() {
  return useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
  });
}

export function useCreateProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<ProviderInfo>) => createProvider(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useUpdateProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, data }: { name: string; data: Partial<ProviderInfo> }) =>
      updateProvider(name, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useDeleteProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteProvider(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function usePlugins() {
  return useQuery({
    queryKey: ["plugins"],
    queryFn: getPlugins,
  });
}
