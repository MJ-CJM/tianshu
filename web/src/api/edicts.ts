import apiClient from "./client";
import type { AcceptanceCriteria, ApiResponse, Edict, EdictCreateRequest, EdictRuntime, EdictStatus, EdictUpdateRequest, GovernanceContractPreview, Memorial, EdictEvent, OuterLoopIteration, SupervisionReport } from "./types";

function withoutServerOwnedIdentity(body: EdictCreateRequest): EdictCreateRequest {
  return Object.fromEntries(
    Object.entries(body).filter(([key]) => key !== "actor" && key !== "submitter"),
  ) as EdictCreateRequest;
}

export async function getOuterLoopIterations(edictId: string): Promise<ApiResponse<OuterLoopIteration[]>> {
  const { data } = await apiClient.get<ApiResponse<OuterLoopIteration[]>>(
    `/edicts/${edictId}/iterations`,
  );
  return data;
}

export async function getSupervisionReports(edictId: string): Promise<SupervisionReport[]> {
  const { data } = await apiClient.get<ApiResponse<SupervisionReport[]>>(
    `/edicts/${edictId}/supervision-reports`,
  );
  return data.data ?? [];
}

/** @deprecated 用 getSupervisionReports 复数版（多监督官） */
export async function getSupervisionReport(edictId: string): Promise<SupervisionReport | null> {
  const list = await getSupervisionReports(edictId);
  return list.length > 0 ? list[0]! : null;
}

export async function createEdict(body: EdictCreateRequest): Promise<ApiResponse<Edict>> {
  const idempotencyKey = body.idempotency_key ?? crypto.randomUUID();
  const safeBody = withoutServerOwnedIdentity(body);
  const { data } = await apiClient.post<ApiResponse<Edict>>(
    "/edicts",
    { ...safeBody, idempotency_key: idempotencyKey },
    { headers: { "Idempotency-Key": idempotencyKey } },
  );
  return data;
}

export async function previewEdictGovernance(
  body: EdictCreateRequest,
): Promise<GovernanceContractPreview> {
  const { data } = await apiClient.post<ApiResponse<GovernanceContractPreview>>(
    "/edicts/governance/preview",
    withoutServerOwnedIdentity(body),
  );
  return data.data!;
}

export async function listEdicts(params: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<ApiResponse<Edict[]>> {
  const { data } = await apiClient.get<ApiResponse<Edict[]>>("/edicts", {
    params,
  });
  return data;
}

export async function getEdict(edictId: string): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.get<ApiResponse<Edict>>(
    `/edicts/${edictId}`,
  );
  return data;
}

export async function getEdictMemorial(
  edictId: string,
): Promise<ApiResponse<Memorial>> {
  const { data } = await apiClient.get<ApiResponse<Memorial>>(
    `/edicts/${edictId}/memorial`,
  );
  return data;
}

export async function getEdictMemorials(
  edictId: string,
): Promise<ApiResponse<Memorial[]>> {
  const { data } = await apiClient.get<ApiResponse<Memorial[]>>(
    `/edicts/${edictId}/memorials`,
  );
  return data;
}

export async function getLatestMemorialsBatch(
  edictIds: string[],
): Promise<ApiResponse<Record<string, Memorial | null>>> {
  const { data } = await apiClient.post<ApiResponse<Record<string, Memorial | null>>>(
    "/edicts/latest-memorials",
    { edict_ids: edictIds },
  );
  return data;
}

export async function getEdictEvents(
  edictId: string,
): Promise<ApiResponse<EdictEvent[]>> {
  const { data } = await apiClient.get<ApiResponse<EdictEvent[]>>(
    `/edicts/${edictId}/events`,
  );
  return data;
}

export type DurableDecisionKind = "tool" | "outer_loop" | "plan_review" | "governed_apply";
export type DurableDecisionStatus = "pending" | "resolved" | "expired" | "cancelled";
export type DurableDecisionAction =
  | "approve"
  | "reject"
  | "guide"
  | "continue"
  | "accept_as_is"
  | "abort"
  | "modify_acceptance";

export interface DurableDecisionRecord {
  request: {
    decision_request_id: string;
    kind: DurableDecisionKind;
    edict_id: string;
    memorial_id: string;
    payload: Record<string, unknown>;
    requested_by: string;
    expires_at: string;
    status: DurableDecisionStatus;
    version: number;
    created_at: string;
    updated_at: string;
  };
  resolution: {
    action: DurableDecisionAction;
    reason: string;
    actor_principal_id: string;
    actor_display_name: string;
    resolved_at: string;
  } | null;
}

export interface EdictRunDetailV1 {
  memorial_id: string;
  phase:
    | "submitted"
    | "planning"
    | "executing"
    | "waiting_decision"
    | "paused"
    | "auditing"
    | "completed"
    | "failed";
  version: number;
  checkpoint_present: boolean;
  side_effect_cursor: number;
  pending_decision_id: string | null;
  resolved_decision_id: string | null;
  plan_lineage: Array<{
    revision_id: string;
    parent_revision_id: string | null;
    plan_hash: string;
    reason_code: string;
    reason_summary: string;
    artifact_digest: string;
    created_at: string;
  }>;
  effective_contract: import("./types").GovernanceEffectiveContract | null;
  updated_at: string;
}

export interface EdictEvidenceDetailV1 {
  bundle_id: string;
  memorial_id: string;
  status: "open" | "closed";
  version: number;
  content_hash: string | null;
  created_at: string;
  closed_at: string | null;
  download_available: boolean;
  executor: {
    adapter_id: string;
    display_name: string;
    level: "managed" | "contained" | "observe-only";
    manifest_hash: string;
  };
  artifacts: Array<{
    digest: string;
    size_bytes: number;
    media_type: string;
    redaction: string;
  }>;
  checks: Array<{
    check_id: string;
    name: string;
    status: "passed" | "failed" | "unavailable" | "skipped";
    command_fingerprint: string | null;
    exit_code: number | null;
    output_artifact_digest: string | null;
    started_at: string;
    completed_at: string;
  }>;
  decisions: Array<{
    decision_request_id: string;
    kind: DurableDecisionKind;
    action: string;
    actor_principal_id: string;
    reason: string;
    payload_hash: string;
    resolved_at: string;
  }>;
  effects: Array<{
    intent_id: string;
    effect_id: string;
    status: "intended" | "receipted" | "uncertain";
    semantics: string;
    reason_code: string | null;
  }>;
  cost: {
    currency: "CNY";
    requested_budget: string | number | null;
    effective_budget: string | number | null;
    actual_cost: string | number;
    prompt_tokens: number;
    completion_tokens: number;
    cache_read_tokens: number;
  };
  environment: {
    tianshu_version: string;
    python_version: string;
    platform: string;
    architecture: string;
    dependency_lock_hash: string;
    environment_fingerprint: string;
  };
  auditor: {
    auditor_id: string;
    verdict: "pass" | "fail";
    reason: string;
    required_evidence: string[];
    missing_evidence: string[];
    evaluated_at: string;
  };
  requirements: {
    check_names: string[];
    decision_request_ids: string[];
    effect_intent_ids: string[];
    artifact_digests: string[];
  };
}

export interface EdictDetailSnapshotV1 {
  schema_version: 1;
  edict: Edict;
  memorials: Memorial[];
  runs: EdictRunDetailV1[];
  decisions: DurableDecisionRecord[];
  evidence: EdictEvidenceDetailV1[];
}

export async function getEdictDetailSnapshot(
  edictId: string,
): Promise<EdictDetailSnapshotV1> {
  const { data } = await apiClient.get<{ data: EdictDetailSnapshotV1 }>(
    `/edicts/${encodeURIComponent(edictId)}/detail`,
  );
  return data.data;
}

function decisionPayload(
  kind: DurableDecisionKind,
  action: DurableDecisionAction,
  reason: string,
): Record<string, unknown> {
  if (kind === "tool" && action === "approve") {
    return { schema_version: 1, grant_scope: "once", grant_reason: reason };
  }
  if (kind === "tool" && action === "guide") {
    return { schema_version: 1, guidance: reason };
  }
  if (kind === "outer_loop" && action === "continue") {
    return { schema_version: 1, feedback: reason };
  }
  return { schema_version: 1 };
}

export interface ResolveEdictDecisionInput {
  decisionRequestId: string;
  kind: DurableDecisionKind;
  action: DurableDecisionAction;
  reason: string;
  expectedVersion: number;
}

export async function resolveEdictDecision(input: ResolveEdictDecisionInput) {
  const { data } = await apiClient.post<{
    data: {
      action: DurableDecisionAction;
      reason: string;
      actor_principal_id: string;
      resolved_at: string;
    };
    status: DurableDecisionStatus;
    version: number;
  }>(`/decisions/${encodeURIComponent(input.decisionRequestId)}/resolve`, {
    action: input.action,
    reason: input.reason,
    expected_version: input.expectedVersion,
    payload: decisionPayload(input.kind, input.action, input.reason),
  });
  return {
    status: data.status,
    version: data.version,
    action: data.data.action,
    reason: data.data.reason,
    actor: data.data.actor_principal_id,
    resolvedAt: data.data.resolved_at,
  };
}

export interface GovernedReplaySource {
  title: string;
  goal: string;
  context: string | null;
  priority: string;
  governanceContract: Record<string, unknown>;
}

export async function replayGovernedEdict(source: GovernedReplaySource): Promise<string> {
  const response = await createEdict({
    title: source.title,
    goal: source.goal,
    context: source.context ?? undefined,
    priority: source.priority,
    governance_contract: source.governanceContract,
  });
  if (!response.data?.id) throw new Error("governed replay did not return an Edict id");
  return response.data.id;
}

export interface FollowUpRequest {
  instruction: string;
  context?: string;
  /** 本次 follow-up 单独覆盖 edict.runtime（仅含填写字段） */
  runtime_override?: Partial<EdictRuntime>;
  /** 本次 follow-up 单独覆盖 edict.acceptance（整体替换） */
  acceptance_override?: AcceptanceCriteria;
}

export async function followUpEdict(
  edictId: string,
  body: FollowUpRequest,
): Promise<ApiResponse<Memorial>> {
  const { data } = await apiClient.post<ApiResponse<Memorial>>(
    `/edicts/${edictId}/follow-up`,
    body,
  );
  return data;
}

export async function updateEdict(
  edictId: string,
  body: EdictUpdateRequest,
): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.patch<ApiResponse<Edict>>(
    `/edicts/${edictId}`,
    body,
  );
  return data;
}

export async function deleteEdict(
  edictId: string,
): Promise<ApiResponse<{ id: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ id: string }>>(
    `/edicts/${edictId}`,
  );
  return data;
}

export async function updateEdictStatus(
  edictId: string,
  status: EdictStatus,
): Promise<ApiResponse<Edict>> {
  const { data } = await apiClient.patch<ApiResponse<Edict>>(
    `/edicts/${edictId}/status`,
    { status },
  );
  return data;
}

export async function pauseEdict(
  edictId: string,
): Promise<ApiResponse<{ id: string; lifecycle_phase: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ id: string; lifecycle_phase: string }>>(
    `/edicts/${edictId}/pause`,
  );
  return data;
}

export async function resumeEdict(
  edictId: string,
): Promise<ApiResponse<{ id: string; lifecycle_phase: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ id: string; lifecycle_phase: string }>>(
    `/edicts/${edictId}/resume`,
  );
  return data;
}

/** steer 中途注入(迭代 5)：向运行中的长任务注入一条纠偏指示，下一轮 actor 吸收 */
export async function steerEdict(
  edictId: string,
  note: string,
): Promise<ApiResponse<{ id: string; steered: boolean }>> {
  const { data } = await apiClient.post<ApiResponse<{ id: string; steered: boolean }>>(
    `/edicts/${edictId}/steer`,
    { note },
  );
  return data;
}

export interface EdictDraft {
  goal?: string;
  title?: string;
  context?: string;
  priority?: string;
  schedule?: {
    type: "immediate" | "once" | "cron";
    cron?: string;
    at?: string;
    timezone?: string;
  };
}

export interface ParseEdictResult {
  draft: EdictDraft;
  notes: string;
}

export async function parseEdict(text: string): Promise<ParseEdictResult> {
  const { data } = await apiClient.post<{ success: boolean; data: ParseEdictResult }>(
    "/edicts/parse",
    { text },
  );
  return data.data;
}
