import apiClient from "./client";
import type { ApiResponse } from "./types";

export interface PolicyEvent {
  id: string | number;
  memorial_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface SessionRule {
  rule_id: string;
  tool_name: string;
  arg_fingerprint: string;
  scope: "edict" | "always";
  edict_id: string | null;
  granted_at: string;
  granted_by_decree_id: string | null;
  source: "approval" | "profile" | "manual";
  reason: string;
  expires_at: string | null;
}

export interface PolicyStats {
  allow: number;
  deny: number;
  require_approval: number;
  approved: number;
  rejected: number;
}

export interface PolicyTemplate {
  name: string;
  allowed_paths: string[];
  allowed_bash_prefixes: string[];
  tier_overrides: Record<string, number>;
  auto_approve_max_tier: number;
}

export async function fetchPolicyEvents(
  edictId: string,
): Promise<PolicyEvent[]> {
  const { data } = await apiClient.get<ApiResponse<{ events: PolicyEvent[] }>>(
    `/edicts/${edictId}/policy_events`,
  );
  return data?.data?.events ?? [];
}

export async function fetchSessionRules(
  scope: "edict" | "always" | "all" = "all",
): Promise<SessionRule[]> {
  const { data } = await apiClient.get<ApiResponse<{ rules: SessionRule[] }>>(
    `/policy/session_rules`,
    { params: { scope } },
  );
  return data?.data?.rules ?? [];
}

export async function revokeSessionRule(ruleId: string): Promise<void> {
  await apiClient.delete(`/policy/session_rules/${ruleId}`);
}

export interface CreateSessionRuleRequest {
  tool_name: string;
  scope: "edict" | "always";
  reason?: string;
  expires_days?: number | null;
  edict_id?: string;
}

export async function createSessionRule(
  body: CreateSessionRuleRequest,
): Promise<ApiResponse<{ rule_id: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ rule_id: string }>>(
    `/policy/session_rules`,
    body,
  );
  return data;
}

export interface ToolInfo {
  name: string;
  description: string;
  tier: number;
}

export async function fetchTools(): Promise<ToolInfo[]> {
  const { data } = await apiClient.get<ApiResponse<ToolInfo[]>>(`/tools`);
  return data?.data ?? [];
}

export async function fetchPolicyStats(): Promise<PolicyStats> {
  const { data } =
    await apiClient.get<ApiResponse<PolicyStats>>(`/policy/stats`);
  return (
    data?.data ?? {
      allow: 0,
      deny: 0,
      require_approval: 0,
      approved: 0,
      rejected: 0,
    }
  );
}

export async function fetchPolicyTemplates(): Promise<PolicyTemplate[]> {
  const { data } =
    await apiClient.get<ApiResponse<{ templates: PolicyTemplate[] }>>(
      `/policy/templates`,
    );
  return data?.data?.templates ?? [];
}
