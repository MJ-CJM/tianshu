import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activateConfig,
  createConfig,
  deleteConfig,
  getAgentConfig,
  getWorkspaceDir,
  getConfig,
  listConfigs,
  updateAgentConfig,
  updateWorkspaceDir,
  updateConfig,
  updateNamedConfig,
} from "../api/config";
import type {
  AgentConfigUpdateRequest,
  LLMConfigCreateRequest,
  LLMConfigUpdateRequest,
} from "../api/types";

const CONFIGS_KEY = ["configs"];
const AGENT_CONFIG_KEY = ["agent-config"];
const WORKSPACE_DIR_KEY = ["workspace-dir"];

// --- Workspace 全局边界 ---

export function useWorkspaceDir() {
  return useQuery({ queryKey: WORKSPACE_DIR_KEY, queryFn: getWorkspaceDir });
}

export function useUpdateWorkspaceDir() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dir: string) => updateWorkspaceDir(dir),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WORKSPACE_DIR_KEY });
    },
  });
}

// --- Agent Config ---

export function useAgentConfig() {
  return useQuery({
    queryKey: AGENT_CONFIG_KEY,
    queryFn: getAgentConfig,
  });
}

export function useUpdateAgentConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: AgentConfigUpdateRequest) => updateAgentConfig(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AGENT_CONFIG_KEY });
    },
  });
}

// --- Legacy (active config) ---

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: LLMConfigUpdateRequest) => updateConfig(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["config"] });
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });
}

// --- Multi-config ---

export function useConfigs() {
  return useQuery({
    queryKey: CONFIGS_KEY,
    queryFn: listConfigs,
  });
}

export function useCreateConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: LLMConfigCreateRequest) => createConfig(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });
}

export function useUpdateNamedConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      req,
    }: {
      name: string;
      req: LLMConfigUpdateRequest;
    }) => updateNamedConfig(name, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });
}

export function useDeleteConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteConfig(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });
}

export function useActivateConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => activateConfig(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONFIGS_KEY });
      queryClient.invalidateQueries({ queryKey: ["config"] });
    },
  });
}
