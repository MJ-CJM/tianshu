import apiClient from "./client";

export type PendingDecisionKind =
  | "tool"
  | "outer_loop"
  | "plan_review"
  | "governed_apply";

export interface PendingDecision {
  decision_request_id: string;
  schema_version: 1;
  kind: PendingDecisionKind;
  edict_id: string;
  memorial_id: string;
  request_key: string;
  payload: Record<string, unknown>;
  payload_hash: string;
  requested_by: string;
  expires_at: string;
  status: "pending";
  version: number;
  created_at: string;
  updated_at: string;
}

interface PendingDecisionsResponse {
  items: PendingDecision[];
  correlation_id: string;
}

export async function listPendingDecisions(limit = 200): Promise<PendingDecision[]> {
  const { data } = await apiClient.get<PendingDecisionsResponse>("/decisions", {
    params: { limit },
  });
  return data.items;
}
