import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listMCPServers,
  listMCPServerTools,
  patchMCPServer,
  deleteMCPOverride,
  reloadMCP,
  createMCPServer,
  type MCPOverridePatch,
  type MCPServerCreate,
} from "../api/mcp";

export function useMCPServers() {
  return useQuery({
    queryKey: ["mcp", "servers"],
    queryFn: listMCPServers,
    select: (data) => data.data ?? [],
    refetchInterval: 5000,
  });
}

export function useMCPServerTools(name: string | null) {
  return useQuery({
    queryKey: ["mcp", "servers", name, "tools"],
    queryFn: () => listMCPServerTools(name!),
    enabled: !!name,
    select: (data) => data.data ?? [],
  });
}

export function usePatchMCPServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, patch }: { name: string; patch: MCPOverridePatch }) =>
      patchMCPServer(name, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp"] });
    },
  });
}

export function useDeleteMCPOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteMCPOverride(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp"] });
    },
  });
}

export function useReloadMCP() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: reloadMCP,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp"] });
      qc.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}

export function useCreateMCPServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MCPServerCreate) => createMCPServer(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp"] });
      qc.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}
