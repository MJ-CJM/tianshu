import type { AxiosRequestConfig } from "axios";
import apiClient from "./client";

export interface EvolutionGateSummaryV1 {
  code: string;
  status: "pending" | "passed" | "failed" | "error" | "missing";
  blocking: boolean;
  current: number | null;
  required: number | null;
  evidence_bundle_id: string | null;
  evidence_hash: string | null;
}

export interface EvolutionCandidateSummaryV1 {
  candidate_id: string;
  kind: "memory" | "skill" | "policy" | "persona" | "code" | "executor";
  version: number;
  lifecycle:
    | "proposed"
    | "staged"
    | "evaluating"
    | "blocked"
    | "ready"
    | "canary"
    | "promoted"
    | "rejected"
    | "rollback_pending"
    | "rolled_back"
    | "archived";
  artifact_hash: string;
  promotion_allowed: boolean;
  rollback_state: "not_required" | "ready" | "pending" | "completed" | "failed";
  gates: EvolutionGateSummaryV1[];
}

export interface EvolutionRoutingSummaryV1 {
  candidate_id: string;
  subject_key: string;
  routing_version: number;
  allocation_percent: number;
  champion_assignment_count: number;
  challenger_assignment_count: number;
}

export interface EvolutionCenterSnapshotV1 {
  schema_version: 1;
  status: "not_enabled" | "enabled" | "degraded";
  reason_code: string;
  routing_enabled: boolean;
  candidates: EvolutionCandidateSummaryV1[];
  routing: EvolutionRoutingSummaryV1[];
  last_gate_hash: string | null;
}

interface EvolutionCenterResponse {
  data: EvolutionCenterSnapshotV1;
  correlation_id: string;
}

export type EvolutionPolicyMode = "frozen" | "manual" | "canary";

export interface EvolutionPolicyV1 {
  subject_key: string;
  kind: "memory" | "skill" | "policy" | "persona" | "code" | "executor";
  mode: EvolutionPolicyMode;
  max_canary_basis_points: number;
  version: number;
  updated_at: string;
}

export interface UpsertEvolutionPolicyV1 {
  subject_key: string;
  kind: EvolutionPolicyV1["kind"];
  mode: EvolutionPolicyMode;
  max_canary_basis_points: number;
  expected_version: number | null;
}

interface EvolutionPolicyResponse {
  data: EvolutionPolicyV1;
  correlation_id: string;
}

interface EvolutionPolicyListResponse {
  data: EvolutionPolicyV1[];
  correlation_id: string;
}

type SilentRequestConfig = AxiosRequestConfig & { silentCodes: number[] };

export async function getEvolutionCenterSnapshot(): Promise<EvolutionCenterSnapshotV1> {
  const response = await apiClient.get<EvolutionCenterResponse>("/evolution");
  return response.data.data;
}

export async function listEvolutionPolicies(): Promise<EvolutionPolicyV1[]> {
  const response = await apiClient.get<EvolutionPolicyListResponse>(
    "/evolution/policies",
    { silentCodes: [403] } as SilentRequestConfig,
  );
  return response.data.data;
}

export async function putEvolutionPolicy(
  policy: UpsertEvolutionPolicyV1,
): Promise<EvolutionPolicyV1> {
  const response = await apiClient.put<EvolutionPolicyResponse>(
    `/evolution/policies/${encodeURIComponent(policy.subject_key)}`,
    {
      kind: policy.kind,
      mode: policy.mode,
      max_canary_basis_points: policy.max_canary_basis_points,
      expected_version: policy.expected_version,
    },
    { silentCodes: [403, 409] } as SilentRequestConfig,
  );
  return response.data.data;
}
