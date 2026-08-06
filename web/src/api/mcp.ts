import apiClient from "./client";
import type { ApiResponse } from "./types";

export interface MCPServerInfo {
  name: string;
  transport: "stdio" | "streamable_http";
  enabled: boolean;
  default_tier: number;
  timeout: number;
  connect_timeout: number;
  status:
    | "connected"
    | "reconnecting"
    | "error"
    | "stopped"
    | "pending"
    | "disabled"
    | "unknown";
  last_error: string | null;
  tools_filter: { include: string[]; exclude: string[] };
  tool_overrides: Record<string, number>;
  // stdio
  command?: string;
  args?: string[];
  env_keys?: string[];
  // streamable_http
  url?: string;
  header_keys?: string[];
  // 仅 list 接口返回
  tools?: { name: string; description: string }[];
}

export interface MCPServerToolInfo {
  name: string;
  full_name: string;
  description: string;
  tier: number;
}

export interface MCPOverridePatch {
  enabled?: boolean | null;
  env?: Record<string, string> | null;
  tools_include?: string[] | null;
  tools_exclude?: string[] | null;
}

export async function listMCPServers(): Promise<ApiResponse<MCPServerInfo[]>> {
  const { data } =
    await apiClient.get<ApiResponse<MCPServerInfo[]>>("/mcp/servers");
  return data;
}

export async function getMCPServer(
  name: string,
): Promise<ApiResponse<MCPServerInfo>> {
  const { data } = await apiClient.get<ApiResponse<MCPServerInfo>>(
    `/mcp/servers/${encodeURIComponent(name)}`,
  );
  return data;
}

export async function listMCPServerTools(
  name: string,
): Promise<ApiResponse<MCPServerToolInfo[]>> {
  const { data } = await apiClient.get<ApiResponse<MCPServerToolInfo[]>>(
    `/mcp/servers/${encodeURIComponent(name)}/tools`,
  );
  return data;
}

export async function patchMCPServer(
  name: string,
  patch: MCPOverridePatch,
): Promise<ApiResponse<{ name: string; note: string }>> {
  const { data } = await apiClient.patch<
    ApiResponse<{ name: string; note: string }>
  >(`/mcp/servers/${encodeURIComponent(name)}`, patch);
  return data;
}

export async function deleteMCPOverride(
  name: string,
): Promise<ApiResponse<{ name: string; note: string }>> {
  const { data } = await apiClient.delete<
    ApiResponse<{ name: string; note: string }>
  >(`/mcp/servers/${encodeURIComponent(name)}/override`);
  return data;
}

export async function reloadMCP(): Promise<
  ApiResponse<{
    servers: number;
    active_sessions: number;
    tools_unregistered: number;
  }>
> {
  const { data } =
    await apiClient.post<
      ApiResponse<{
        servers: number;
        active_sessions: number;
        tools_unregistered: number;
      }>
    >("/mcp/reload");
  return data;
}

export interface MCPServerCreate {
  name: string;
  transport: "stdio" | "streamable_http";
  enabled?: boolean;
  default_tier?: number;
  timeout?: number;
  connect_timeout?: number;
  // stdio
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  // streamable_http
  url?: string;
  headers?: Record<string, string>;
  // filter
  tools_include?: string[];
  tools_exclude?: string[];
  tool_overrides?: Record<string, number>;
}

export async function createMCPServer(
  body: MCPServerCreate,
): Promise<
  ApiResponse<{ name: string; status: string; last_error: string | null }>
> {
  const { data } = await apiClient.post<
    ApiResponse<{ name: string; status: string; last_error: string | null }>
  >("/mcp/servers", body);
  return data;
}
