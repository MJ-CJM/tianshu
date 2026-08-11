import apiClient from "./client";
import type {
  AgentConfig,
  AgentConfigUpdateRequest,
  ApiResponse,
  KeqingStatusData,
  LLMConfig,
  LLMConfigCreateRequest,
  LLMConfigListResponse,
  LLMConfigUpdateRequest,
  WorkspaceDirInfo,
} from "./types";

// --- 客卿(keqing)健康状态 ---

export async function getKeqingStatus(): Promise<KeqingStatusData> {
  const { data } = await apiClient.get<ApiResponse<KeqingStatusData>>("/keqing/status");
  return data.data!;
}

// --- Workspace 全局边界 ---

export async function getWorkspaceDir(): Promise<WorkspaceDirInfo> {
  const { data } = await apiClient.get<ApiResponse<WorkspaceDirInfo>>("/workspace");
  return data.data!;
}

export async function updateWorkspaceDir(workspace_dir: string): Promise<WorkspaceDirInfo> {
  const { data } = await apiClient.put<ApiResponse<WorkspaceDirInfo>>("/workspace", {
    workspace_dir,
  });
  return data.data!;
}

// --- Agent Config ---

export async function getAgentConfig(): Promise<AgentConfig> {
  const { data } = await apiClient.get<ApiResponse<AgentConfig>>("/agent-config");
  return data.data!;
}

export async function updateAgentConfig(
  req: AgentConfigUpdateRequest,
): Promise<AgentConfig> {
  const { data } = await apiClient.put<ApiResponse<AgentConfig>>(
    "/agent-config",
    req,
  );
  return data.data!;
}

// --- Legacy single-config (operates on active config) ---

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

// --- Multi-config ---

export async function listConfigs(): Promise<LLMConfigListResponse> {
  const { data } =
    await apiClient.get<ApiResponse<LLMConfigListResponse>>("/configs");
  return data.data!;
}

export async function createConfig(
  req: LLMConfigCreateRequest,
): Promise<LLMConfig> {
  const { data } = await apiClient.post<ApiResponse<LLMConfig>>(
    "/configs",
    req,
  );
  return data.data!;
}

export async function updateNamedConfig(
  name: string,
  req: LLMConfigUpdateRequest,
): Promise<LLMConfig> {
  const { data } = await apiClient.put<ApiResponse<LLMConfig>>(
    `/configs/${encodeURIComponent(name)}`,
    req,
  );
  return data.data!;
}

export async function deleteConfig(name: string): Promise<void> {
  await apiClient.delete(`/configs/${encodeURIComponent(name)}`);
}

export async function activateConfig(
  name: string,
): Promise<LLMConfigListResponse> {
  const { data } = await apiClient.put<ApiResponse<LLMConfigListResponse>>(
    `/configs/${encodeURIComponent(name)}/activate`,
  );
  return data.data!;
}
