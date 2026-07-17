import apiClient from "./client";

export type ControlRunPhase =
  | "submitted"
  | "planning"
  | "executing"
  | "waiting_decision"
  | "paused"
  | "auditing"
  | "completed"
  | "failed";

export interface ControlRunSummaryV1 {
  edict_id: string;
  edict_title: string;
  memorial_id: string;
  phase: ControlRunPhase;
  updated_at: string;
}

export interface ControlDecisionSummaryV1 {
  decision_request_id: string;
  edict_id: string;
  edict_title: string;
  memorial_id: string;
  kind: "tool" | "outer_loop" | "plan_review" | "governed_apply";
  expires_at: string;
  created_at: string;
}

export interface ControlEvidenceSummaryV1 {
  bundle_id: string;
  edict_id: string;
  edict_title: string;
  memorial_id: string;
  status: "open" | "closed";
  content_hash: string | null;
  created_at: string;
  closed_at: string | null;
}

export interface ControlCenterSnapshotV1 {
  schema_version: 1;
  generated_at: string;
  readiness: "ready" | "degraded";
  active_runs: ControlRunSummaryV1[];
  pending_decisions: ControlDecisionSummaryV1[];
  recent_evidence: ControlEvidenceSummaryV1[];
  evolution_status: "not_enabled" | "enabled" | "degraded";
}

interface ControlCenterResponse {
  data: ControlCenterSnapshotV1;
  correlation_id: string;
}

export async function getControlCenterSnapshot(): Promise<ControlCenterSnapshotV1> {
  const response = await apiClient.get<ControlCenterResponse>("/control");
  return response.data.data;
}
