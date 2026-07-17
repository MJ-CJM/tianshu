import apiClient from "./client";

export interface EvolutionGateSummaryV1 {
  code: string;
  status: "pending" | "passed" | "failed" | "error" | "missing";
  blocking: boolean;
  current: number | null;
  required: number | null;
  evidence_hash: string | null;
  evidence_uri: string | null;
}

export interface EvolutionCandidateSummaryV1 {
  candidate_id: string;
  kind: "memory" | "skill" | "policy" | "persona" | "code";
  version: number;
  lifecycle:
    | "draft"
    | "staged"
    | "evaluating"
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
  routing_version: number;
  allocation_percent: number;
  champion_assignment_count: number;
  challenger_assignment_count: number;
}

export interface EvolutionCenterSnapshotV1 {
  schema_version: 1;
  status: "not_enabled" | "enabled" | "degraded";
  reason_code: string;
  candidates: EvolutionCandidateSummaryV1[];
  routing: EvolutionRoutingSummaryV1[];
  last_gate_hash: string | null;
}

interface EvolutionCenterResponse {
  data: EvolutionCenterSnapshotV1;
  correlation_id: string;
}

export async function getEvolutionCenterSnapshot(): Promise<EvolutionCenterSnapshotV1> {
  const response = await apiClient.get<EvolutionCenterResponse>("/evolution");
  return response.data.data;
}
