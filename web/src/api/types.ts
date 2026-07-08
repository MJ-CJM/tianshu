export type TaskStatus =
  | "submitted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "scheduled"
  | "planning"
  | "auditing"
  | "needs_review";

export interface UsageSummary {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens?: number;
  cost_cny?: number;
  actual_model?: string | null;
  upstream_provider?: string | null;
}

export type EdictStatus = "open" | "completed" | "cancelled";

export interface EdictSchedule {
  type: "immediate" | "once" | "cron";
  at: string | null;
  cron: string | null;
  timezone: string;
}

export interface EdictPolicyProfile {
  template_name: string | null;
  allowed_paths: string[];
  allowed_bash_prefixes: string[];
  tier_overrides: Record<string, number>;
  auto_approve_max_tier: number;
  expires_after_seconds: number | null;
}

export interface EdictRuntime {
  timeout_seconds: number;
  max_iterations: number;
  max_concurrency: number;
  retry_limit: number;
  token_budget: number | null;
  cost_budget_cny: number | null;
  approval_required_tools: string[];
  policy_profile?: EdictPolicyProfile | null;
  api_request_hosts?: string[];
  api_request_write_hosts?: string[];
  lifecycle_phase: "active" | "paused" | "winding_down" | "complete";
  /** 迭代 3.5：执行 backend（native | keqing:claude-code | keqing:codex） */
  executor?: string;
}

export interface ShadowSnapshot {
  id: string;
  edict_id: string;
  memorial_id: string | null;
  sha: string;
  label: string;
  work_tree: string;
  created_at: string;
}

export interface ArtifactRef {
  name: string;
  type: string;
  path: string | null;
  url: string | null;
}

export interface TimelineItem {
  timestamp: string;
  event: string;
  detail: string | null;
}

export interface SchedulerJob {
  job_id: string;
  edict_id: string;
  schedule_type: string;
  next_run: string | null;
}

export interface Edict {
  id: string;
  title: string;
  goal: string;
  context: string | null;
  status: EdictStatus;
  created_at: string;
  priority: "urgent" | "normal" | "low";
  review_policy: "never" | "on_failure" | "on_flag" | "always";
  schedule: EdictSchedule;
  runtime: EdictRuntime;
  constraints: string[];
  output_format: string | null;
  source: string;
  submitter: string | null;
  assigned_persona_id?: string | null;
  planner_persona_id?: string | null;
  plan_review?: boolean;
  acceptance?: AcceptanceCriteria | null;
  execution_profile?: ExecutionProfile;
}

export interface AuditResult {
  verdict: "pass" | "flag" | "block";
  reasons: string[];
  rules_checked: number;
  llm_reviewed: boolean;
}

export interface Memorial {
  id: string;
  edict_id: string;
  instruction: string | null;
  status: TaskStatus;
  summary: string | null;
  result: string | null;
  usage: UsageSummary;
  error: string | null;
  /** 失败原因分类（迭代 2）：status=failed 时落库自动归因，非失败为 null */
  failure_reason?: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  attempt: number;
  parent_memorial_id: string | null;
  review_status: "not_required" | "pending" | "approved" | "rejected";
  audit: AuditResult | null;
  artifacts: ArtifactRef[];
  timeline: TimelineItem[];
  persona_id: string | null;
  dag_node_id: string | null;
}

export interface EdictEvent {
  id: string;
  event_type: string;
  created_at: string;
  edict_id: string;
  memorial_id: string | null;
  payload: Record<string, unknown>;
}

export interface CheckSpec {
  kind: "bash" | "lint" | "rubric";
  name: string;
  command?: string;
  rubric?: string;
  weight?: number;
  pass_threshold?: number;
  timeout_seconds?: number;
}

export interface CriticSpec {
  persona_ids?: string[];
  persona_id?: string | null;
  model?: string | null;
  same_issue_threshold?: number;
  /** lenient=合格即 pass / balanced=高标准 / strict=优秀才 pass */
  strictness?: "lenient" | "balanced" | "strict";
}

export interface EscalationSpec {
  enabled_levels?: ("L1" | "L2" | "L3")[];
  l1_max_rounds?: number;
  l2_max_rounds?: number;
  l1_thinking_budget?: number;
  l1_model_upgrade?: string | null;
  l2_consultation_personas?: string[];
}

export interface AcceptanceCriteria {
  checks?: CheckSpec[];
  critic?: CriticSpec;
  escalation?: EscalationSpec;
  /** 最少迭代轮数（≥2 = 持续优化模式，即使 critic pass 也强制继续） */
  min_outer_iterations?: number;
  max_outer_iterations?: number;
  deadline_seconds?: number | null;
  on_exhaustion?: "escalate" | "best_effort" | "fail";
  on_critic_unavailable?: "escalate" | "skip";
  on_approval_timeout?: "fail" | "best_effort";
}

export type ExecutionProfile = "foreground" | "checkpointed" | "background";

export interface EdictCreateRequest {
  goal: string;
  title?: string;
  context?: string;
  idempotency_key?: string;
  submitter?: string;
  schedule?: { type: string; cron?: string; at?: string };
  priority?: string;
  review_policy?: string;
  constraints?: string[];
  output_format?: string;
  runtime?: Partial<EdictRuntime>;
  assigned_persona_id?: string | null;
  planner_persona_id?: string | null;
  plan_review?: boolean;
  acceptance?: AcceptanceCriteria | null;
  execution_profile?: ExecutionProfile;
}

export interface OuterLoopIteration {
  id: string;
  edict_id: string;
  iteration: number;
  level: "L0" | "L1" | "L2" | "L3";
  actor_output: string | null;
  checks_result: string | null;  // JSON string
  critic_result: string | null;  // JSON string
  cost_cny: number;
  started_at: string;
  finished_at: string;
  archived_at: string | null;
}

/** 长任务终态后由监督官 (critic persona) 产出的总评报告 */
export interface SupervisionReport {
  edict_id: string;
  memorial_id: string;
  persona_id: string;
  persona_name: string;
  final_status: "completed" | "failed" | "cancelled" | "running" | "submitted";
  iterations_count: number;
  total_cost_cny: number;
  issues_observed: string[];
  well_done: string[];
  poorly_done: string[];
  recommendation: string | null;
  raw_feedback: string;
  created_at: string;
}

export interface EdictUpdateRequest {
  title?: string;
  goal?: string;
  context?: string;
}

export interface Decree {
  id: string;
  memorial_id: string;
  action: "approve" | "reject" | "retry" | "amend" | "cancel";
  comment: string | null;
  amended_goal: string | null;
  actor: string;
  created_at: string;
}

export interface DecreeCreateRequest {
  memorial_id: string;
  action: string;
  comment?: string;
  amended_goal?: string;
  actor?: string;
}

export type ToolGrantScope = "once" | "edict" | "always";

export interface PendingToolCall {
  memorial_id: string;
  edict_id: string;
  tool_name: string;
  rule_id: string | null;
  reason: string | null;
  tool_tier: string | null;
  args_summary: Record<string, unknown>;
  created_at: string | null;
}

export interface ToolDecisionRequest {
  memorial_id: string;
  action: "approve" | "reject";
  comment?: string;
  actor?: string;
  grant_scope?: ToolGrantScope;
  grant_reason?: string;
}

export interface WsMessage {
  type: string;
  edict_id?: string;
  memorial_id?: string;
  status?: string;
  [key: string]: unknown;
}

export interface LLMConfig {
  name: string;
  model: string;
  api_key_masked: string;
  api_base: string;
  max_retries: number;
  temperature: number;
  top_p: number;
  max_tokens: number;
  enabled: boolean;
}

export interface LLMConfigCreateRequest {
  name: string;
  model: string;
  api_key?: string;
  api_base?: string;
  max_retries?: number;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  enabled?: boolean;
}

export interface LLMConfigUpdateRequest {
  model?: string;
  api_key?: string;
  api_base?: string;
  max_retries?: number;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  enabled?: boolean;
}

export interface LLMConfigListResponse {
  configs: LLMConfig[];
  active_name: string;
}

export interface AgentConfig {
  agent_max_iterations: number;
  agent_timeout_seconds: number;
  agent_max_concurrency: number;
  agent_retry_limit: number;
  agent_token_budget: number | null;
  agent_cost_budget_cny: number | null;
  skills_char_budget: number;
}

export interface AgentConfigUpdateRequest {
  agent_max_iterations?: number;
  agent_timeout_seconds?: number;
  agent_max_concurrency?: number;
  agent_retry_limit?: number;
  agent_token_budget?: number | null;
  agent_cost_budget_cny?: number | null;
  skills_char_budget?: number;
}

// --- Audit types ---

export interface AuditStatsSummary {
  total_memorials: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  audit_pass: number;
  audit_flag: number;
  audit_block: number;
  review_pending: number;
  review_approved: number;
  review_rejected: number;
}

export interface EdictUsageRow {
  edict_id: string;
  edict_title: string;
  priority: string;
  token_budget: number | null;
  memorial_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface RecentAuditRow {
  memorial_id: string;
  edict_id: string;
  edict_title: string;
  verdict: "pass" | "flag" | "block";
  reasons: string[];
  rules_checked: number;
  llm_reviewed: boolean;
  review_status: string | null;
  completed_at: string | null;
}

export interface AuditStats {
  summary: AuditStatsSummary;
  per_edict: EdictUsageRow[];
  recent_audits: RecentAuditRow[];
}

export interface ApiResponse<T> {
  success: boolean;
  data: T | null;
  error: string | null;
  metadata: {
    total: number;
    limit: number;
    offset: number;
  } | null;
}

// --- Cost types ---

export interface CostRecord {
  id: string;
  edict_id: string;
  memorial_id: string | null;
  provider_name: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens?: number;
  cost_cny: number;
  created_at: string;
}

export interface CostSummary {
  total_records: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_cost_cny: number;
}

export interface BudgetStatus {
  scope: string;
  budget_cny: number;
  spent_cny: number;
  remaining_cny: number;
  period: "daily" | "weekly" | "monthly";
  exceeded: boolean;
}

// --- Provider types ---

export interface ProviderInfo {
  name: string;
  model: string;
  api_base: string | null;
  capabilities: string[];
  status: "active" | "degraded" | "disabled";
  priority: number;
  rpm_limit: number | null;
  tpm_limit: number | null;
  rpm_current: number;
  tpm_current: number;
  cost_per_1k_prompt: number | null;
  cost_per_1k_completion: number | null;
  cost_per_1k_cache_read: number | null;
  created_at: string;
}

/** Provider 当前生效的 3 维价（来自 GET /providers/:name/pricing/effective） */
export interface EffectivePricing {
  miss: number | null;
  hit: number | null;
  out: number | null;
  /** custom = 三维都自定义；default = 三维都未自定义；mixed = 部分自定义 */
  source: "custom" | "default" | "mixed";
}

export interface ProviderPricingUpdate {
  cost_per_1k_prompt?: number | null;
  cost_per_1k_cache_read?: number | null;
  cost_per_1k_completion?: number | null;
}

/** 默认价表条目（GET /providers/pricing/defaults） */
export interface DefaultPricingEntry {
  model: string;
  miss: number;
  hit: number;
  out: number;
}

export interface DefaultPricingTable {
  entries: DefaultPricingEntry[];
  fallback: { miss: number; hit: number; out: number };
}

// --- Plugin types ---

export interface PluginInfo {
  name: string;
  version: string;
  manifest: Record<string, unknown>;
  status: string;
  sha256: string | null;
  installed_at: string;
  updated_at: string | null;
}

// --- DAG types (Phase 3) ---

export type DAGNodeStatus = "pending" | "ready" | "running" | "completed" | "failed" | "cancelled";

export interface DAGNode {
  node_id: string;
  dag_execution_id: string;
  description: string;
  depends_on: string[];
  status: DAGNodeStatus;
  assigned_official: string | null;
  assigned_worker: string | null;
  tools_required: string[];
  memorial_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface DAGExecution {
  id: string;
  edict_id: string;
  plan_json: string;
  status: string;
  root_memorial_id: string | null;
  max_concurrency: number;
  created_at: string;
  completed_at: string | null;
  nodes: DAGNode[];
}

export interface DepartmentInfo {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface DepartmentCreateRequest {
  id: string;
  name: string;
  description?: string;
}

export interface DepartmentUpdateRequest {
  name?: string;
  description?: string;
}

export interface PersonaInfo {
  id: string;
  name: string;
  department: string;
  department_name?: string;
  title?: string | null;
  tools_allowed: string[];
  tools_denied: string[];
  skills_allowed: string[];
  tool_tier_max: number;
  can_delegate: boolean;
  memory_global_read: boolean;
  delegates_to: string[];
  llm_config_name?: string | null;
}

export interface PersonaCreateRequest {
  id: string;
  name: string;
  department: string;
  title?: string | null;
  tools_allowed?: string[];
  tools_denied?: string[];
  skills_allowed?: string[];
  tool_tier_max?: number;
  can_delegate?: boolean;
  memory_global_read?: boolean;
  delegates_to?: string[];
  llm_config_name?: string | null;
  /** Optional role-template seed (see persona-templates endpoints). */
  template_id?: string;
  template_lang?: "zh" | "en";
}

export interface PersonaTemplateInfo {
  id: string;
  name: string;
  description: string;
  emoji: string;
}

export interface PersonaTemplateCategory {
  category: string;
  templates: PersonaTemplateInfo[];
}

export interface PersonaTemplateDetail {
  id: string;
  lang: "zh" | "en";
  category: string;
  name: string;
  description: string;
  emoji: string;
  soul_preview: string;
  role_preview: string;
}

export interface PersonaUpdateRequest {
  name?: string;
  department?: string;
  title?: string | null;
  tools_allowed?: string[];
  tools_denied?: string[];
  skills_allowed?: string[];
  tool_tier_max?: number;
  can_delegate?: boolean;
  memory_global_read?: boolean;
  delegates_to?: string[];
  llm_config_name?: string | null;
}

export interface MemorialBrief {
  id: string;
  instruction: string | null;
  status: TaskStatus;
  result: string | null;
  summary: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface EdictMemorialGroup {
  edict_id: string;
  edict_title: string;
  edict_goal: string;
  edict_status: string;
  memorials: MemorialBrief[];
}

export interface PersonaMetrics {
  persona_id: string;
  total_executions: number;
  completed: number;
  failed: number;
  cancelled: number;
  success_rate: number;
  total_tokens: number;
  avg_tokens_per_execution: number;
  total_cost_cny: number;
  avg_duration_seconds: number;
}

// --- Consultation types (Phase 3) ---

export interface ConsultationRequest {
  topic: string;
  context?: string;
  edict_id?: string;
  persona_ids: string[];
  synthesizer_persona_id?: string;
}

export interface PersonaOpinion {
  persona_id: string;
  persona_name: string;
  department: string;
  opinion: string;
  confidence: number;
  key_points: string[];
}

export type ConsultationStatus = "pending" | "running" | "completed" | "failed";

export interface ConsultationResponse {
  id: string;
  status: ConsultationStatus;
  request: ConsultationRequest | null;
  opinions: PersonaOpinion[];
  synthesis: string | null;
  decision: string | null;
  created_at: string;
  completed_at: string | null;
}

// --- Memory types ---

export interface MemoryEntry {
  id: string;
  persona_id: string;
  edict_id: string | null;
  memorial_id: string | null;
  category: "observation" | "insight" | "entity" | "summary";
  content: string;
  source: "agent" | "compaction" | "reflection";
  confidence: number;
  entity_refs: string[];
  created_at: string;
  expires_at: string | null;
  access_level: "private" | "shared" | "court";
}

// --- System management types (藏兵阁) ---

export interface SkillInfo {
  name: string;
  description: string;
  source: "builtin" | "user" | "workspace" | "injected";
  always: boolean;
  tool_tier: string | null;
  path: string;
  content_length: number;
  // Phase 8 metrics fields (agent-created skills only)
  created_by?: string;
  state?: string;
  pinned?: boolean;
  human_curated?: boolean;
  usage_count?: number;
  success_rate?: number | null;
  created_at?: string;
}

export interface SkillDetail extends SkillInfo {
  content: string;
}

// Alias used by the skills control panel (Phase 8)
export type SkillMeta = SkillInfo;

export interface ToolInfo {
  name: string;
  description: string;
  tier: number;
  parameters: Record<string, unknown>;
  personas: string[];
  enabled: boolean;
}

export interface PromptFileInfo {
  persona_id: string;
  filename: string;
  path: string;
  size: number;
  modified: string;
}

export interface PromptFilesResponse {
  files: PromptFileInfo[];
  departments: Record<string, string>;
}

export interface PromptFileContent {
  persona_id: string;
  filename: string;
  content: string;
}

// --- Ops Monitor types (运维监控台) ---

export interface EventBusHandler {
  handler: string;
  priority: number;
}

export interface EventBusHandlers {
  [eventType: string]: EventBusHandler[];
}

export interface EventBusStats {
  [eventType: string]: number;
}

export interface RecentEvent {
  id: string;
  edict_id: string;
  memorial_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface HookRegistryEntry {
  handler: string;
  priority: number;
}

export interface HooksRegistry {
  [hookType: string]: HookRegistryEntry[];
}

export interface NotificationChannel {
  name: string;
  type: string;
  rpm_limit: number;
  recent_sends: number;
}

export interface MemoryStats {
  [personaId: string]: {
    entry_count: number;
    total_chars: number;
    estimated_tokens: number;
    by_category: Record<string, number>;
    markdown_files: number;
    markdown_size_bytes: number;
  };
}

export interface PromptLayer {
  layer: number;
  name: string;
  source: string;
  chars: number;
  tokens_est: number;
  char_budget?: number;
  filtered_by?: string[] | null;
}

export interface PromptLayersResponse {
  persona_id: string | null;
  total_chars: number;
  total_tokens_est: number;
  layers: PromptLayer[];
}

export interface RoutingRule {
  persona_id: string;
  name: string;
  department: string;
  preferred_department: string;
  is_fallback: boolean;
}

export interface KeywordRule {
  name: string;
  department: string;
  keywords: string[];
}

export interface DelegationChain {
  from_id: string;
  from_name: string;
  delegates_to: string[];
}

export interface RoutingRules {
  default_map: Record<string, RoutingRule>;
  keyword_map: Record<string, KeywordRule>;
  delegation_chains: DelegationChain[];
}

export interface AuditRuleInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  severity: string;
}

export interface ReviewPolicyInfo {
  value: string;
  label: string;
  description: string;
}

export interface AuditRulesResponse {
  rules: AuditRuleInfo[];
  review_policies: ReviewPolicyInfo[];
}

// --- Planner stats types ---

export interface PlannerHistoryItem {
  edict_id: string;
  title: string;
  goal: string;
  assigned_persona_id: string | null;
  planner_persona_id: string | null;
  plan_type: "passthrough" | "dag";
  task_count: number;
  created_at: string;
}

export interface PlannerStats {
  total_edicts: number;
  passthrough_count: number;
  dag_count: number;
  avg_tasks_per_dag: number;
  recent_history: PlannerHistoryItem[];
}

export interface CompactionResult {
  status: string;
  reason?: string;
  original_count?: number;
  compacted_count?: number;
  summary?: string;
  tokens_saved?: number;
}

export interface ReflectionResult {
  status: string;
  reason?: string;
  insights_generated?: number;
  insights?: string[];
}

// --- Universe types (平行位面 Phase 8) ---

export interface Universe {
  id: string;
  name: string;
  parent_universe_id: string | null;
  status: "champion" | "challenger" | "archived";
  origin: "genesis" | "manual_branch" | "mutation" | "code_variant";
  mutation_reason: string | null;
  description: string;
  /** 数值型适应度分项 + 可能混入的 truncated（预算截断）布尔标记 */
  fitness: Record<string, number | boolean>;
  created_at: string;
  code_ref: string | null;
}

export interface VariantEvalRun {
  id: string;
  universe_id: string;
  gate_passed: boolean;
  gate_detail: { stage?: string; detail?: string } | null;
  /** 数值型适应度分项 + 可能混入的 truncated（预算截断）布尔标记 */
  fitness: Record<string, number | boolean>;
  /** 同评估集下的冠军基线分（纯数值，无 truncated 混入）；无基线或基线被截断时为 null */
  baseline?: Record<string, number> | null;
  eval_set_version: string | null;
  cost: number;
  created_at: string;
}

// --- External Network Credentials (Spec 2026-04-22 §4) ---
export interface Credential {
  id: string;
  name: string;
  host_pattern: string;
  header_template: string;
  extra_headers: Record<string, string>;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  kind: "edict_auth" | "engine_provider";
  provider_name: string | null;
  enabled: boolean;
}

export interface CredentialCreate {
  name: string;
  value: string;
  kind?: "edict_auth" | "engine_provider";
  host_pattern?: string;
  header_template?: string;
  extra_headers?: Record<string, string>;
  provider_name?: "jina" | "tavily" | "firecrawl";
}

export interface CredentialUpdate {
  value?: string;
  extra_headers?: Record<string, string>;
  enabled?: boolean;
}

// --- Network events (鸿胪寺 audit) ---
export interface NetworkEventRow {
  event_id: string;
  created_at: string;
  edict_id: string;
  edict_title: string | null;
  tool: "web_fetch" | "web_search" | "api_request" | "web_extract" | string;
  host: string | null;
  method: string | null;
  http_status: number | null;
  bytes_out: number | null;
  credential_name: string | null;
  cached: boolean;
  is_error: boolean;
  reason: string | null;
  provider: string | null;
  result_count: number | null;
  truncated: boolean;
}

// --- Platform evals & failure attribution (迭代 2「证明」) ---

export interface EvalGoalResult {
  instruction: string | null;
  status: string;
  error: string | null;
  failure_reason: string | null;
  cost: number;
}

export interface EvalFitness {
  score: number;
  samples: number;
  success_rate: number;
  audit_rate: number;
  retry_score: number;
  cost_score: number;
  feedback: number;
}

export interface EvalRunBrief {
  id: string;
  eval_set_name: string | null;
  eval_set_fingerprint: string;
  target: string;
  fitness: EvalFitness;
  n: number;
  truncated: boolean;
  delta_vs_prev: number | null;
  created_at: string;
}

export interface EvalRun extends EvalRunBrief {
  stats: Record<string, number>;
  goal_results: EvalGoalResult[];
  failure_distribution: FailureDistributionItem[];
}

export interface EvalSet {
  name: string;
  goals: string[];
  source: string;
  created_at: string;
}

export interface FailureDistributionItem {
  reason: string;
  count: number;
  last_seen?: string | null;
}
